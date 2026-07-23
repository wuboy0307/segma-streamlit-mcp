#!/usr/bin/env python3
"""Headless transcript generator for segma-streamlit-mcp.

Runs the SAME Agent the Streamlit app uses (SYSTEM_PROMPT + gpt-4o -> Pydantic AI
-> segma MCP) over a scripted list of user turns, and writes the run out in the
examples/session-*.md format. This is a REAL run: real LLM, real MCP, real
backend mutations — the transcript validates that the prompt templates actually
work against real data.

Convention (Ernest): **example turns come FROM the app's PROMPT_TEMPLATES.** A
turns file lists, per turn, WHICH template to use + the values to drop into its
`< >` placeholders; the generator pulls the live template text out of
`streamlit_app.py`, fills it, and runs it. So an example can never drift from the
template it documents — and if a needed template is missing, this errors, which
is the signal to add it to PROMPT_TEMPLATES first.

Turns file = JSON list; each item is one of:
  - {"template": ["<category>", "<label>"], "fill": {"<placeholder>": "value"}}
      resolve that template's text from PROMPT_TEMPLATES[lang], str-replace each
      fill key. Leftover `< >` after filling -> error (unless "allow_unfilled").
  - ["label", "literal prompt"]   (legacy escape hatch; not template-checked)

Secrets never printed. Connection values for a build example come from the
gitignored segma-backend/spec/e2e/config/data_sources.yml — fill them in the
turns file's `fill` map; the committed turns/example keep placeholders only if
you choose, but a real run needs the real values.

Usage (from repo root):
    LLM_API_KEY=... .venv/bin/python tools/gen_transcript.py \
        --turns tools/turns/session-10-segments.json \
        --title "Segments from templates" \
        --base "既有的 mcpdemo_ 信用卡 CDP(分析主體『mcpdemo_持卡人』+ 指標若干)" \
        --out examples/session-10-segments.md

Token/URL resolution: env SEGMA_MCP_URL / SEGMA_MCP_TOKEN win; else ~/.claude.json.
"""
import argparse
import ast
import datetime
import json
import os
import sys


# --- reuse a top-level literal from the app without executing its UI ---------
def _load_app_literal(app_path: str, name: str):
    src = open(app_path, encoding="utf-8").read()
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise SystemExit(f"{name} not found in {app_path}")


def load_system_prompt(app_path: str) -> dict:
    return _load_app_literal(app_path, "SYSTEM_PROMPT")


def load_prompt_templates(app_path: str) -> dict:
    return _load_app_literal(app_path, "PROMPT_TEMPLATES")


# --- MCP url + token: env override, else ~/.claude.json ----------------------
def load_mcp() -> tuple[str, str]:
    env_url = os.environ.get("SEGMA_MCP_URL", "").strip()
    env_tok = os.environ.get("SEGMA_MCP_TOKEN", "").strip()
    if env_url and env_tok:
        return env_url, env_tok

    d = json.load(open(os.path.expanduser("~/.claude.json")))

    def find(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "segma" and isinstance(v, dict) and "headers" in v:
                    return v
                r = find(v)
                if r:
                    return r
        return None

    cfg = find(d) or {}
    url = env_url or cfg.get("url", "https://localhost:1443/mcp")
    token = env_tok or (cfg.get("headers") or {}).get("Authorization", "").replace("Bearer ", "").strip()
    return url, token


# --- load .env (LLM creds) without printing ----------------------------------
def load_env(path: str) -> None:
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


# --- resolve turns FROM templates -------------------------------------------
def _find_template(templates: dict, lang: str, category: str, label: str) -> str:
    cats = templates.get(lang) or {}
    if category not in cats:
        raise SystemExit(
            f"template category not found: {category!r} (have: {list(cats)}). "
            "Add it to PROMPT_TEMPLATES in streamlit_app.py first."
        )
    for lbl, text in cats[category]:
        if lbl == label:
            return text
    have = [lbl for lbl, _ in cats[category]]
    raise SystemExit(
        f"template not found: [{category!r}, {label!r}]; that category has: {have}. "
        "Add it to PROMPT_TEMPLATES first, or fix the label."
    )


def resolve_turns(raw: list, templates: dict, lang: str) -> list[tuple[str, str]]:
    out = []
    for item in raw:
        if isinstance(item, list):  # legacy [label, prompt]
            out.append((item[0], item[1]))
            continue
        cat, label = item["template"]
        text = _find_template(templates, lang, cat, label)
        for k, v in (item.get("fill") or {}).items():
            text = text.replace(k, str(v))
        if not item.get("allow_unfilled"):
            import re
            leftover = re.findall(r"<[^<>]{1,40}>", text)
            if leftover:
                raise SystemExit(
                    f"unfilled placeholders in template [{cat}, {label}]: {leftover}. "
                    "Add them to this turn's `fill` map."
                )
        out.append((item.get("label") or f"{cat} · {label}", text))
    return out


# --- extract_tool_calls: capture ToolReturn AND RetryPrompt (failures) --------
def extract_tool_calls(new_messages):
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart, RetryPromptPart

    outcome = {}  # tool_call_id -> (content, ok)
    for msg in new_messages:
        for part in getattr(msg, "parts", []):
            if isinstance(part, ToolReturnPart):
                outcome[part.tool_call_id] = (part.content, _content_ok(part.content))
            elif isinstance(part, RetryPromptPart):
                tcid = getattr(part, "tool_call_id", None)
                if tcid is not None:
                    outcome[tcid] = (getattr(part, "content", "retry"), False)
    calls = []
    for msg in new_messages:
        for part in getattr(msg, "parts", []):
            if isinstance(part, ToolCallPart):
                content, ok = outcome.get(part.tool_call_id, (None, True))
                calls.append({"name": part.tool_name, "args": part.args, "result": content, "ok": ok})
    return calls


def _content_ok(result) -> bool:
    if result is None:
        return True
    text = result if isinstance(result, str) else json.dumps(result, default=str)
    low = text.lower()
    return not any(m in low for m in ('"code": 4', '"code":4', '"code": 5', '"code":5',
                                      "http error", '"errors": ["', '"errors":["',
                                      "缺少", "not one of", "不支持", "not support", "does not support"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", default="streamlit_app.py")
    ap.add_argument("--env", default=".env")
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--turns", required=True,
                    help="JSON turns file (template-sourced; see module docstring)")
    ap.add_argument("--title", required=True, help="example title (goes in the H1)")
    ap.add_argument("--base", default="既有的 mcpdemo_ 信用卡 demo",
                    help="the '基底 =' clause: what the run builds on (mcpdemo demo, or a fresh warehouse)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-tokens", type=int, default=4096)
    args = ap.parse_args()

    sys.path.insert(0, os.getcwd())
    from agent_runtime import build_agent  # no Streamlit UI executed

    load_env(args.env)
    api_key = os.environ.get("LLM_API_KEY", "")
    model = os.environ.get("LLM_MODEL", "gpt-4o")
    base_url = os.environ.get("LLM_BASE_URL", "")
    if not api_key:
        raise SystemExit("LLM_API_KEY missing — put it in .env (gitignored).")

    system_prompt = load_system_prompt(args.app)[args.lang]
    templates = load_prompt_templates(args.app)
    mcp_url, token = load_mcp()
    if not token:
        raise SystemExit("no MCP token (set SEGMA_MCP_TOKEN or ~/.claude.json segma headers)")

    raw = json.load(open(args.turns, encoding="utf-8"))
    turns = resolve_turns(raw, templates, args.lang)

    # require_confirm=False: a transcript run must let the agent act freely (no
    # human-approval gate) — the gate is a UI feature, not part of validation.
    agent = build_agent(mcp_url=mcp_url, token=token, model_name=model, api_key=api_key,
                        base_url=base_url, verify=False, max_tokens=args.max_tokens,
                        instructions=system_prompt, require_confirm=False)

    print(f"model={model} mcp={mcp_url} turns={len(turns)} lang={args.lang}", file=sys.stderr)

    lines = [f"# Example session — {args.title} via MCP\n"]
    lines.append("真實測試:對話**逐字取自 app 左側的常用範本**(`< >` 佔位符填入真實資料),"
                 "驅動 app 同一套 Agent 全程跑一遍——所以這份記錄同時是**範本的驗證**。"
                 "過程中遇到的 MCP bug / 缺漏(若有)見文末。\n")
    lines.append(f"> 建構器 = segma-streamlit-mcp 的同一套 SYSTEM_PROMPT + Agent"
                 f"({model} → Pydantic AI → segma MCP)。目標 = 本機 stack。基底 = {args.base}。\n")

    history = []
    for i, (label, prompt) in enumerate(turns, 1):
        print(f"[turn {i}/{len(turns)}] {label} ...", file=sys.stderr)
        result = agent.run_sync(prompt, message_history=history)
        calls = extract_tool_calls(result.new_messages())
        # Every ❌ is a defect (unclear MCP description or SYSTEM_PROMPT) — surface the
        # real error so it can be root-caused, not just retried past.
        for c in calls:
            if not c.get("ok", True):
                err = c["result"]
                err = err if isinstance(err, str) else json.dumps(err, ensure_ascii=False, default=str)
                print(f"    ❌ {c['name']}: {err[:500]}", file=sys.stderr)
        history = history + result.new_messages()
        output = getattr(result, "output", None) or getattr(result, "data", "")

        lines.append(f"\n## Turn {i} — 使用者({label})\n")
        lines.append(f"> {prompt}\n")
        if calls:
            lines.append("\n**工具呼叫**:\n")
            for c in calls:
                mark = "✅" if c.get("ok", True) else "❌"
                lines.append(f"- {mark} `{c['name']}`")
            lines.append("")
        lines.append(f"\n**助手**:{output}\n")

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append(f"\n---\n\n_Generated {ts} by tools/gen_transcript.py "
                 f"(headless, real run; turns 取自 PROMPT_TEMPLATES)._\n")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"wrote {args.out} ({len(turns)} turns)", file=sys.stderr)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Run the app's prompt templates through LOCAL Claude and report what happened.

Same templates the app offers and the same turns files `gen_transcript.py` uses,
but driven by headless `claude -p` (subscription) instead of the OpenAI-billed
agent. Purpose is validation, not producing an example transcript: did each
template execute, and did resources actually get created / edited / deleted.

Template text is resolved through gen_transcript, so it cannot drift from
PROMPT_TEMPLATES — a turns file naming a template that no longer exists errors.

    tools/run_templates_local.py --turns tools/turns/session-04-segments.json
    tools/run_templates_local.py --all                # every turns file
    tools/run_templates_local.py --all --dry-run      # resolve only, no agent

Verification per turn:
  - every MCP tool call, and any that came back an error
  - an inventory delta over the resource types, taken before and after, so
    "created / edited / deleted" is read off the backend rather than off the
    agent's own summary — which has been wrong before.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MCP_REPO = REPO.parent / "segma-mcp"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(MCP_REPO))

import httpx  # noqa: E402

from gen_transcript import load_mcp, load_prompt_templates, resolve_turns  # noqa: E402
from tests.prompt_eval.agent import run_agent  # noqa: E402

# Sensitive connection values are deliberately absent from the committed turns
# files; each such turn's `_comment` points at the gitignored
# segma-backend/spec/e2e/config/data_sources.yml. Supply them at run time so they
# stay out of git, matching placeholders by what they ask for.
SECRET_KEYWORDS = {
    "warehouse_host": ("主機", "host"),
    "warehouse_port": ("埠", "port"),
    "warehouse_user": ("帳號", "user"),
    "warehouse_password": ("密碼", "password"),
    "warehouse_database": ("資料庫名", "database"),
    "warehouse_schema": ("schema",),
}


def load_secrets(path: Path, wanted_type: str) -> dict[str, str]:
    """Connection params for one warehouse type, as {param: value}."""
    import yaml

    if not path.exists():
        return {}
    for entry in yaml.safe_load(path.read_text()) or []:
        if isinstance(entry, dict) and entry.get("type") == wanted_type:
            params = entry.get("params") or {}
            return {k: str(v) for k, v in params.items() if v not in (None, "")}
    return {}


def fill_secrets(raw_turns: list, templates: dict, lang: str, secrets: dict) -> int:
    """Add secret values to each turn's `fill` for placeholders it left open."""
    import re

    if not secrets:
        return 0
    added = 0
    for item in raw_turns:
        if not isinstance(item, dict) or "template" not in item:
            continue
        cat, label = item["template"]
        text = next((t for lbl, t in templates[lang].get(cat, []) if lbl == label), "")
        fill = item.setdefault("fill", {})
        for placeholder in re.findall(r"<[^>]+>", text):
            if placeholder in fill:
                continue
            asked = placeholder.strip("<>").lower()
            for param, words in SECRET_KEYWORDS.items():
                if param in secrets and any(w.lower() in asked for w in words):
                    fill[placeholder] = secrets[param]
                    added += 1
                    break
    return added


# Resources the turns files create are prefixed per session (b_ build,
# d_ demo, z_ zodiac, mcpdemo_ the shared demo CDP).
NAME_PREFIXES = ("b_", "d_", "z_", "mcpdemo_")

RESOURCE_TYPES = [
    "data_sources", "dims", "facts", "traits", "metrics", "segments",
    "action_datasets", "destinations", "syncs", "feature_stores",
]


def inventory(api_base: str, token: str) -> dict[str, set[int] | None]:
    """Ids per resource type, so a delta shows what appeared and disappeared.

    Uses the search endpoint: the plain index paginates, and a page-1 read would
    miss a freshly created record and report it as never created.

    A type that could not be read becomes None, NOT an empty set. Swallowing the
    error into `set()` made a transient backend 500 look like "399 traits were
    deleted" — a fabricated catastrophe in the report while the database was
    untouched. Unknown must stay distinguishable from empty.
    """
    out: dict[str, set[int] | None] = {}
    with httpx.Client(base_url=api_base, verify=False, timeout=60.0,
                      headers={"Authorization": f"Bearer {token}",
                               "Content-Type": "application/json"}) as client:
        for rtype in RESOURCE_TYPES:
            try:
                r = client.post(f"/{rtype}/search", json={"q": {}, "limit": 500})
                r.raise_for_status()
                out[rtype] = {row["id"] for row in r.json().get("data", [])}
            except (httpx.HTTPError, ValueError):
                out[rtype] = None
    return out


def delta(before: dict, after: dict) -> tuple[dict[str, dict], list[str]]:
    """(changes, types that could not be compared)."""
    changed, unknown = {}, []
    for rtype in RESOURCE_TYPES:
        b, a = before.get(rtype), after.get(rtype)
        if b is None or a is None:
            unknown.append(rtype)
            continue
        created, deleted = sorted(a - b), sorted(b - a)
        if created or deleted:
            changed[rtype] = {"created": created, "deleted": deleted}
    return changed, unknown


def expected_names(raw_item: dict) -> list[str]:
    """Resource names this turn names in its `fill` map.

    An inventory delta alone cannot tell "created it" from "did nothing" when the
    resource already exists from an earlier run — `z_星座` was already there from
    July, so the turn showed no change while working correctly. Checking the named
    record answers the real question: is it there, and did this run touch it.
    """
    out = []
    for value in (raw_item.get("fill") or {}).values():
        v = str(value).strip().strip("『』「」\"\'")
        # Only the session prefixes name resources these turns create. Taking
        # every short fill value instead reported `amount`, `customer_id` and
        # `credit_card.transaction_history` as missing resources — they are
        # column and table names, and the false alarms buried the real ones.
        if any(v.startswith(pfx) for pfx in NAME_PREFIXES) and len(v) <= 60:
            out.append(v)
    return sorted(set(out))


def find_named(api_base: str, token: str, name: str) -> list[tuple[str, int, str]]:
    """Every resource of any type carrying this exact name."""
    hits = []
    with httpx.Client(base_url=api_base, verify=False, timeout=60.0,
                      headers={"Authorization": f"Bearer {token}",
                               "Content-Type": "application/json"}) as client:
        for rtype in RESOURCE_TYPES:
            try:
                r = client.post(f"/{rtype}/search", json={"q": {"name_eq": name}, "limit": 5})
                if r.status_code != 200:
                    continue
                for row in r.json().get("data", []):
                    hits.append((rtype, row["id"], str(row.get("updated_at") or "")))
            except httpx.HTTPError:
                continue
    return hits


def tool_errors(run) -> list[str]:
    """Tool results that came back an error, read off the raw event stream."""
    found = []
    for event in run.raw_events:
        if event.get("type") != "user":
            continue
        for block in (event.get("message") or {}).get("content", []):
            if block.get("type") != "tool_result":
                continue
            body = block.get("content")
            text = json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body
            if block.get("is_error") or '"code":4' in text or '"code":5' in text:
                found.append(text[:300])
    return found


def run_session(turns_path: Path, mcp_url: str, token: str, api_base: str,
                templates: dict, lang: str, max_turns: int, dry_run: bool,
                secrets: dict | None = None) -> dict:
    raw = json.load(open(turns_path, encoding="utf-8"))
    if secrets:
        n = fill_secrets(raw, templates, lang, secrets)
        if n:
            print(f"  (supplied {n} connection value(s) from data_sources.yml)")
    resolved = resolve_turns(raw, templates, lang)
    print(f"\n=== {turns_path.name}: {len(resolved)} turns ===", flush=True)

    session = {"file": turns_path.name, "turns": []}
    if dry_run:
        for label, prompt in resolved:
            print(f"  [resolved] {label}: {prompt[:70].replace(chr(10), ' ')}…")
            session["turns"].append({"label": label, "resolved": True})
        return session

    raw_items = [x for x in raw if isinstance(x, dict) and "template" in x]
    claude_session = None
    for i, (label, prompt) in enumerate(resolved, 1):
        wanted = expected_names(raw_items[i - 1]) if i - 1 < len(raw_items) else []
        before = inventory(api_base, token)
        t0 = time.time()
        run = run_agent(prompt, mcp_url, token, max_turns=max_turns,
                        resume_session=claude_session)
        claude_session = run.session_id or claude_session
        after = inventory(api_base, token)

        errors = tool_errors(run)
        changes, unknown = delta(before, after)
        named = {n: find_named(api_base, token, n) for n in wanted}
        missing = [n for n, hits in named.items() if not hits]
        row = {
            "label": label,
            "tool_calls": [t.name.removeprefix("mcp__segma__") for t in run.tool_calls],
            "errors": errors,
            "changes": changes,
            "is_error": run.is_error,
            "named_present": {n: [f"{t}#{i}" for t, i, _ in h] for n, h in named.items()},
            "named_missing": missing,
            "unreadable_types": unknown,
            "turns": run.num_turns,
            "input_tokens": run.input_tokens,
            "output_tokens": (run.usage or {}).get("output_tokens"),
            "cost_usd": run.cost_usd,
            "seconds": round(time.time() - t0, 1),
        }
        session["turns"].append(row)

        summary = ", ".join(
            f"{rt}:+{len(v['created'])}" + (f"/-{len(v['deleted'])}" if v["deleted"] else "")
            for rt, v in changes.items()
        ) or "no resource change"
        if unknown:
            summary += f"  (could not read: {', '.join(unknown)})"
        if missing:
            summary += f"  MISSING: {', '.join(missing)}"
        flag = "ERR " if (errors or run.is_error or missing) else "ok  "
        tok = f"{row['input_tokens']:,}" if row["input_tokens"] else "?"
        print(f"  {flag}[{i}/{len(resolved)}] {label[:40]:<40} "
              f"{len(row['tool_calls']):>2} calls  {row['seconds']:>5.1f}s  "
              f"{tok:>9} in-tok  {summary}", flush=True)
        for e in errors[:2]:
            print(f"        error: {e[:160]}", flush=True)
    return session


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", action="append", default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--app", default=str(REPO / "streamlit_app.py"))
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=str(REPO / "tools" / "template_run_report.json"))
    ap.add_argument("--secrets-from",
                    default=str(REPO.parent / "segma-backend/spec/e2e/config/data_sources.yml"),
                    help="gitignored warehouse credentials, for the connect templates")
    ap.add_argument("--secrets-type", default="postgres")
    args = ap.parse_args()

    files = [Path(p) for p in args.turns]
    if args.all or not files:
        files = sorted((REPO / "tools" / "turns").glob("*.json"))

    templates = load_prompt_templates(args.app)
    mcp_url, token = load_mcp()
    api_base = mcp_url.rstrip("/").removesuffix("/mcp") + "/api/v1"
    print(f"mcp={mcp_url}  turns files={len(files)}  lang={args.lang}"
          f"{'  (dry run)' if args.dry_run else ''}")

    secrets = load_secrets(Path(args.secrets_from), args.secrets_type)
    print(f"connection secrets for {args.secrets_type!r}: "
          f"{'loaded' if secrets else 'NOT FOUND — connect templates will report unfilled'}")

    sessions = [run_session(f, mcp_url, token, api_base, templates, args.lang,
                            args.max_turns, args.dry_run, secrets) for f in files]

    if not args.dry_run:
        rows = [t for s in sessions for t in s["turns"]]
        bad = [t for t in rows if t.get("errors") or t.get("is_error") or t.get("named_missing")]
        touched = [t for t in rows if t.get("changes")]
        print(f"\n{len(rows)} template turns: {len(rows) - len(bad)} clean, {len(bad)} with errors; "
              f"{len(touched)} changed backend state")
        if bad:
            print("turns with errors:")
            for t in bad:
                print(f"  - {t['label']}")

        priced = [t for t in rows if t.get("input_tokens")]
        if priced:
            total_in = sum(t["input_tokens"] for t in priced)
            print(f"\ntokens per template (input, descending) — total {total_in:,} "
                  f"over {len(priced)} turns, mean {total_in // len(priced):,}:")
            for t in sorted(priced, key=lambda x: -x["input_tokens"]):
                print(f"  {t['input_tokens']:>9,}  {len(t['tool_calls']):>2} calls  {t['label']}")
        Path(args.out).write_text(json.dumps(sessions, ensure_ascii=False, indent=2))
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

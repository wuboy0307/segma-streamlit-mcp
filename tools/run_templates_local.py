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
import re
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
from template_expectations import (  # noqa: E402
    check_categories_mapped, expected_type, wrong_type,
)
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


# Reverse dependency order: a segment references traits and metrics, a trait
# references a fact and a dim, and those reference a data source. Deleting the
# other way round just collects `restrict_with_error`.
#
# traits BEFORE metrics: `Trait belongs_to :metric` (that is `trait_type:
# 'metric'`) and `Metric has_many :traits, dependent: :restrict_with_error`, so
# a metric still wrapped by a trait cannot go first. These turns do build such a
# trait, and with metrics first the refusal cascades — dim blocked by the
# metric, data source blocked by the dim — leaving a wipe that reports success
# and removed almost nothing.
#
# Established from the model definitions, not from the incident that prompted
# the look: the partial wipe on 2026-08-12 had a different cause (a leftover
# `replay_` segment holding the same metric), and re-ordering did not resolve
# it. Worth stating plainly, because "changed X, symptom gone" is the story
# this comment would otherwise imply, and that story would be false.
DELETE_ORDER = [
    "syncs", "feature_stores", "action_datasets", "segments",
    "traits", "metrics", "facts", "dims", "destinations", "data_sources",
]


def wipe_prefix(api_base: str, token: str, prefix: str) -> tuple[dict[str, int], list[str]]:
    """Delete this session's own resources so the run has to create them again.

    Without it the turns are idempotent against artefacts an earlier example run
    left behind — the agent finds `b_客戶` already there, correctly declines to
    make a second one, and the report shows "no resource change" for a template
    that works. Only the session prefixes are touched; `mcpdemo_` is the shared
    demo CDP other eval cases build on and is never in scope here.

    Returns (removed, survivors). The survivors half is not optional detail: a
    failed DELETE (`restrict_with_error` from a dependant this pass did not
    reach, a permission refusal) used to be swallowed, so the run announced
    `wiped 'b_'` while three resources were still standing — and the turns that
    then found their resource already present were indistinguishable, in the
    report, from turns that created it. That is the very confusion `--fresh`
    exists to remove, so a partial wipe has to say so out loud.
    """
    assert prefix and prefix != "mcpdemo_", f"refusing to wipe prefix {prefix!r}"
    removed: dict[str, int] = {}
    with httpx.Client(base_url=api_base, verify=False, timeout=60.0,
                      headers={"Authorization": f"Bearer {token}",
                               "Content-Type": "application/json"}) as client:
        for rtype in DELETE_ORDER:
            try:
                r = client.post(f"/{rtype}/search",
                                json={"q": {"name_start": prefix}, "limit": 100})
                if r.status_code != 200:
                    continue
                for row in r.json().get("data", []):
                    d = client.delete(f"/{rtype}/{row['id']}")
                    if d.status_code < 300:
                        removed[rtype] = removed.get(rtype, 0) + 1
            except httpx.HTTPError:
                continue

        # Read back rather than trust the delete responses — the question is
        # what is still there, and only a re-read answers that.
        survivors = []
        for rtype in DELETE_ORDER:
            try:
                r = client.post(f"/{rtype}/search",
                                json={"q": {"name_start": prefix}, "limit": 100})
                if r.status_code != 200:
                    survivors.append(f"{rtype}:unreadable")
                    continue
                for row in r.json().get("data", []):
                    survivors.append(f"{rtype}#{row['id']} {row.get('name')}")
            except httpx.HTTPError:
                survivors.append(f"{rtype}:unreadable")
    return removed, survivors


def session_prefix(raw_turns: list) -> str | None:
    """The prefix this turns file creates under, from its own fill values."""
    for item in raw_turns:
        if not isinstance(item, dict):
            continue
        for value in (item.get("fill") or {}).values():
            v = str(value).strip().strip("『』「」")
            for pfx in NAME_PREFIXES:
                if pfx != "mcpdemo_" and v.startswith(pfx):
                    return pfx
    return None


def inventory(api_base: str, token: str) -> dict[str, dict[int, str] | None]:
    """{id: name} per resource type, so a delta shows what appeared and disappeared.

    Uses the search endpoint: the plain index paginates, and a page-1 read would
    miss a freshly created record and report it as never created.

    A type that could not be read becomes None, NOT an empty dict. Swallowing the
    error into `{}` made a transient backend 500 look like "399 traits were
    deleted" — a fabricated catastrophe in the report while the database was
    untouched. Unknown must stay distinguishable from empty.

    Names, not just ids, because this stack is shared. The inventory is taken over
    the WHOLE backend, so anything a parallel session creates between the two
    reads is attributed to whichever turn was running. That is not hypothetical:
    on 2026-08-12 a turn building a trait was reported as `destinations:+1`, and
    the destination was another session's `probe-verify-0812`. Filtering by
    prefix would be wrong — the real turns also create unprefixed records, such as
    the 15 traits a dim generates from its columns — so the report names what
    appeared and lets the reader see it was not ours.
    """
    out: dict[str, dict[int, str] | None] = {}
    with httpx.Client(base_url=api_base, verify=False, timeout=60.0,
                      headers={"Authorization": f"Bearer {token}",
                               "Content-Type": "application/json"}) as client:
        for rtype in RESOURCE_TYPES:
            try:
                r = client.post(f"/{rtype}/search", json={"q": {}, "limit": 500})
                r.raise_for_status()
                out[rtype] = {row["id"]: str(row.get("name") or "")
                              for row in r.json().get("data", [])}
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
        created, deleted = sorted(set(a) - set(b)), sorted(set(b) - set(a))
        if created or deleted:
            changed[rtype] = {
                "created": created,
                "deleted": deleted,
                "created_names": [a.get(i, "") for i in created],
                "deleted_names": [b.get(i, "") for i in deleted],
            }
    return changed, unknown


def expected_names(raw_item: dict) -> list[str]:
    """Resource names this turn names in its `fill` map.

    An inventory delta alone cannot tell "created it" from "did nothing" when the
    resource already exists from an earlier run — `z_星座` was already there from
    July, so the turn showed no change while working correctly. Checking the named
    record answers the real question: is it there, and did this run touch it.
    """
    # Split on every separator and quote a fill can use, then keep only clean
    # name-shaped tokens. Several fills are prose that MENTIONS resources —
    # "『d_2022年總消費』介於 30000 到 999999999" is a condition description, not a
    # name. Taking the whole value reported unfindable "names" that were really
    # sentences, and those false alarms outnumbered the real misses.
    tokens = []
    for value in (raw_item.get("fill") or {}).values():
        tokens.extend(re.split(r"[、,，;；\s『』「」\"']+", str(value)))
    out = []
    for token in tokens:
        v = token.strip()
        if not (2 <= len(v) <= 40):
            continue
        if not any(v.startswith(pfx) for pfx in NAME_PREFIXES):
            continue
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


def result_bytes(run) -> tuple[int, int]:
    """(total chars of tool results, largest single result).

    A turn costs far more input than the ~50k of tool definitions — the rest is
    conversation history, and tool results are the part of it that grows. Sizing
    them says whether the next saving is in the definitions or in what the tools
    hand back.
    """
    total = biggest = 0
    for event in run.raw_events:
        if event.get("type") != "user":
            continue
        for block in (event.get("message") or {}).get("content", []):
            if block.get("type") != "tool_result":
                continue
            body = block.get("content")
            text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            total += len(text)
            biggest = max(biggest, len(text))
    return total, biggest


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


def model_used(run) -> str | None:
    """The model the run ACTUALLY used, read off its own init event.

    Not the string that was passed in. Passing `--model` and then recording the
    variable you passed produces a report that cannot disagree with you — the
    A/B on 2026-08-12 mislabelled every row exactly this way, because each row
    carried the loop variable rather than what the service was running. A
    mistyped or unavailable alias silently falls back, and a fallback that
    reports itself as the weak model is a measurement of the wrong thing.
    """
    for event in run.raw_events:
        if event.get("type") == "system" and event.get("model"):
            return event["model"]
    return None


def check_model(requested: str | None, served: str | None) -> str | None:
    """Complaint text if the run did not use the model that was asked for.

    A full id (`claude-haiku-4-5-20251001`) has to come back verbatim; a short
    alias (`haiku`) only has to appear in what came back, since the CLI expands
    it to a dated id. Either way the comparison is against the served string —
    the point is to catch a silent fallback, which otherwise produces a run
    labelled "weak model" that was nothing of the sort.
    """
    if not requested:
        return None
    if not served:
        return f"asked for {requested!r} but the run never reported a model"
    if requested.startswith("claude-"):
        return None if served == requested else f"asked for {requested!r}, ran {served!r}"
    return None if requested in served else f"asked for {requested!r}, ran {served!r}"


def run_session(turns_path: Path, mcp_url: str, token: str, api_base: str,
                templates: dict, lang: str, max_turns: int, dry_run: bool,
                secrets: dict | None = None, fresh: bool = False,
                model: str | None = None, out_path: Path | None = None,
                all_sessions: list | None = None) -> dict:
    raw = json.load(open(turns_path, encoding="utf-8"))
    unmapped = check_categories_mapped(raw)
    if unmapped:
        raise SystemExit(
            f"{turns_path.name}: no expected resource type for {unmapped} — add them to "
            "CATEGORY_EXPECTS (None if the category only reads). Left unmapped, the "
            "wrong-type check quietly skips those turns and they read as passes."
        )
    if fresh:
        pfx = session_prefix(raw)
        if pfx:
            gone, left = wipe_prefix(api_base, token, pfx)
            print(f"  (wiped {pfx!r}: {gone or 'nothing to remove'})")
            if left:
                # Loud, because every downstream "no resource change" for one of
                # these means "it was already there", not "the template did
                # nothing" — and the two look identical in the table.
                print(f"  !! {len(left)} {pfx!r} resource(s) SURVIVED the wipe — turns "
                      f"touching them are NOT measuring creation:")
                for s in left:
                    print(f"       {s}")
                # Say what to do, not just that it went wrong — the same rule
                # this project applies to its own agent-facing errors. The usual
                # cause is another turns file building on top of this one: a
                # `d_` dim sitting on the `b_` data source keeps that data source
                # undeletable, and no amount of re-running THIS prefix helps.
                others = [p for p in NAME_PREFIXES if p not in (pfx, "mcpdemo_")]
                print(f"     -> most often another turns file's resources are built on "
                      f"top of these. Wipe {', '.join(repr(p) for p in others)} first, "
                      f"then {pfx!r} again, and re-run.")
        else:
            print("  (no session prefix found — nothing wiped)")
    if secrets:
        n = fill_secrets(raw, templates, lang, secrets)
        if n:
            print(f"  (supplied {n} connection value(s) from data_sources.yml)")
    resolved = resolve_turns(raw, templates, lang)
    print(f"\n=== {turns_path.name}: {len(resolved)} turns ===", flush=True)

    session = {"file": turns_path.name, "model_requested": model, "turns": []}
    if all_sessions is not None:
        all_sessions.append(session)
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
                        resume_session=claude_session, model=model)
        claude_session = run.session_id or claude_session
        after = inventory(api_base, token)

        errors = tool_errors(run)
        results_chars, biggest_result = result_bytes(run)
        # Pair each tool_use id with the result that came back for it.
        result_sizes: dict[str, int] = {}
        error_ids: dict[str, bool] = {}
        for event in run.raw_events:
            if event.get("type") != "user":
                continue
            for block in (event.get("message") or {}).get("content", []):
                if block.get("type") != "tool_result":
                    continue
                payload = block.get("content")
                if isinstance(payload, list):
                    payload = " ".join(
                        b.get("text", "") for b in payload if isinstance(b, dict)
                    )
                result_sizes[block.get("tool_use_id", "")] = len(str(payload or ""))
                error_ids[block.get("tool_use_id", "")] = bool(block.get("is_error"))
        changes, unknown = delta(before, after)
        named = {n: find_named(api_base, token, n) for n in wanted}
        missing = [n for n, hits in named.items() if not hits]
        served = model_used(run)
        named_types = {n: [f"{t}#{i}" for t, i, _ in h] for n, h in named.items()}
        mistyped = wrong_type(
            expected_type(raw_items[i - 1]) if i - 1 < len(raw_items) else None,
            named_types,
        )
        row = {
            "label": label,
            # What ran, read back — see model_used(). Kept per turn rather than
            # per session because `--resume` carries the model from the resumed
            # session, so turn 1 does not settle it for turns 2..n.
            "model": served,
            "tool_calls": [t.name.removeprefix("mcp__segma__") for t in run.tool_calls],
            # Arguments too. A turn that creates the same resource twice is the
            # expensive shape, and only the arguments say what the agent changed
            # on the retry — which is what a tool description would have to
            # state up front to save the round trip.
            # …and the SIZE of what each one returned. Without it a report can
            # rank turns by cost but not say which call carried it, and the
            # first attempt at trimming payloads was aimed by guesswork
            # because this column was all zeros.
            #
            # A call that ERRORED keeps its arguments in full. Truncating those
            # at 400 characters cost a root cause on 2026-08-12: a create_trait
            # answered 500, the retry succeeded, and the only difference visible
            # inside the cut was one added field — which turned out not to be
            # the trigger at all. The payload that would have settled it was
            # past the cut, and by the time that was noticed the backend log was
            # unreadable too, so the defect could not be explained. The failing
            # call is exactly the one worth keeping whole.
            "calls_detail": [
                {"tool": t.name.removeprefix("mcp__segma__"),
                 "args": (json.dumps(t.input, ensure_ascii=False)
                          if error_ids.get(getattr(t, "id", ""), False)
                          else json.dumps(t.input, ensure_ascii=False)[:400]),
                 "result_chars": result_sizes.get(getattr(t, "id", ""), 0),
                 "is_error": error_ids.get(getattr(t, "id", ""), False)}
                for t in run.tool_calls
            ],
            "errors": errors,
            "changes": changes,
            "is_error": run.is_error,
            "named_present": {n: [f"{t}#{i}" for t, i, _ in h] for n, h in named.items()},
            "named_missing": missing,
            "wrong_type": mistyped,
            "unreadable_types": unknown,
            "turns": run.num_turns,
            "input_tokens": run.input_tokens,
            "result_chars": results_chars,
            "biggest_result_chars": biggest_result,
            "output_tokens": (run.usage or {}).get("output_tokens"),
            "cost_usd": run.cost_usd,
            "seconds": round(time.time() - t0, 1),
        }
        session["turns"].append(row)
        # Flush after EVERY turn, not once at the end. A 16-turn run stopped at
        # turn 15 on 2026-08-12 wrote nothing at all, and the per-call
        # trajectories — the whole reason the run was made — were gone; only
        # what had scrolled past on the console survived. A run this long is
        # exactly the one that gets interrupted, so its output cannot be
        # all-or-nothing.
        if out_path is not None and all_sessions is not None:
            out_path.write_text(json.dumps(all_sessions, ensure_ascii=False, indent=2))

        def named_delta(v):
            # Name the first few, so a record this session did not create is
            # recognisable on the line rather than only in the JSON.
            shown = ", ".join(n for n in v.get("created_names", [])[:3] if n)
            more = len(v["created"]) - 3
            return f" ({shown}{f' +{more}' if more > 0 else ''})" if shown else ""

        summary = ", ".join(
            f"{rt}:+{len(v['created'])}{named_delta(v)}"
            + (f"/-{len(v['deleted'])}" if v["deleted"] else "")
            for rt, v in changes.items()
        ) or "no resource change"
        if unknown:
            summary += f"  (could not read: {', '.join(unknown)})"
        if missing:
            summary += f"  MISSING: {', '.join(missing)}"
        if mistyped:
            summary += f"  WRONG TYPE: {mistyped}"
        flag = "ERR " if (errors or run.is_error or missing or mistyped) else "ok  "
        tok = f"{row['input_tokens']:,}" if row["input_tokens"] else "?"
        print(f"  {flag}[{i}/{len(resolved)}] {label[:40]:<40} "
              f"{len(row['tool_calls']):>2} calls  {row['seconds']:>5.1f}s  "
              f"{tok:>9} in-tok  {summary}", flush=True)
        for e in errors[:2]:
            print(f"        error: {e[:160]}", flush=True)
        # Abort rather than carry on: a run measured against the wrong model is
        # not a weaker result, it is a different experiment, and it is
        # indistinguishable from a good one once it is in the report.
        complaint = check_model(model, served)
        if complaint:
            raise SystemExit(f"model mismatch on turn {i} ({label}): {complaint}")
    return session


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", action="append", default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--app", default=str(REPO / "streamlit_app.py"))
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fresh", action="store_true",
                    help="delete each session's own prefixed resources first, so the "
                         "run has to create them and creation is actually verified. "
                         "Never touches mcpdemo_.")
    ap.add_argument("--out", default=str(REPO / "tools" / "template_run_report.json"))
    ap.add_argument("--secrets-from",
                    default=str(REPO.parent / "segma-backend/spec/e2e/config/data_sources.yml"),
                    help="gitignored warehouse credentials, for the connect templates")
    ap.add_argument("--secrets-type", default="postgres")
    ap.add_argument("--model", default=None,
                    help="model for the agent, e.g. claude-haiku-4-5-20251001 — the "
                         "weak-model stand-in these measurements are supposed to use. "
                         "Omitted means whatever `claude -p` defaults to, which is "
                         "stronger and not comparable with a weak-model baseline.")
    args = ap.parse_args()

    files = [Path(p) for p in args.turns]
    if args.all or not files:
        files = sorted((REPO / "tools" / "turns").glob("*.json"))

    templates = load_prompt_templates(args.app)
    mcp_url, token = load_mcp()
    api_base = mcp_url.rstrip("/").removesuffix("/mcp") + "/api/v1"
    print(f"mcp={mcp_url}  turns files={len(files)}  lang={args.lang}  "
          f"model={args.model or 'DEFAULT (not the weak-model baseline)'}"
          f"{'  (dry run)' if args.dry_run else ''}")

    secrets = load_secrets(Path(args.secrets_from), args.secrets_type)
    print(f"connection secrets for {args.secrets_type!r}: "
          f"{'loaded' if secrets else 'NOT FOUND — connect templates will report unfilled'}")

    out_path = None if args.dry_run else Path(args.out)
    sessions: list = []
    for f in files:
        run_session(f, mcp_url, token, api_base, templates, args.lang,
                    args.max_turns, args.dry_run, secrets, args.fresh,
                    args.model, out_path, sessions)

    if not args.dry_run:
        rows = [t for s in sessions for t in s["turns"]]
        bad = [t for t in rows if t.get("errors") or t.get("is_error")
               or t.get("named_missing") or t.get("wrong_type")]
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
            print(f"  {'in-tok':>9} {'calls':>6} {'result chars':>13} {'biggest':>9}  template")
            for t in sorted(priced, key=lambda x: -x["input_tokens"]):
                print(f"  {t['input_tokens']:>9,} {len(t['tool_calls']):>6} "
                      f"{t.get('result_chars', 0):>13,} {t.get('biggest_result_chars', 0):>9,}  "
                      f"{t['label']}")
            tot_res = sum(t.get("result_chars", 0) for t in priced)
            print(f"\ntool results totalled {tot_res:,} chars (~{tot_res // 4:,} tok) across "
                  f"{len(priced)} turns — compare with {total_in:,} input tokens billed")
        # Already written after each turn; this is the final consistent copy.
        out_path.write_text(json.dumps(sessions, ensure_ascii=False, indent=2))
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Turn a template run report into the list of things worth fixing.

Input tokens for a turn are roughly (number of model requests) x (context size),
and the context is re-sent on every step — so the cost of a template is driven by
how many steps the agent needs, not by how much data it moves.

The ranking that matters is therefore steps per template, and the outliers are
prompts or tool descriptions that make the agent hunt.

One earlier reading of this has to be flagged, because it is the obvious one and
it is wrong: comparing the run's total tool-result chars against its total input
tokens (84k chars vs 1.5M tokens, "1.4%, so payload size hardly matters") counts
each result ONCE. The runner resumes the session, so a result returned at step k
of an N-step session is re-sent by every later step, and its real cost is size x
(N-k). That does not overturn the conclusion — steps still dominate — but it is
not a licence to treat payload as free, and it is why the payload table below
ranks by total chars returned rather than by call count.

    tools/analyze_template_run.py [report.json] [--trace N]

`--trace N` prints the N costliest turns call by call — the view that says
whether a repeated tool was progress or thrashing.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from template_expectations import CATEGORY_EXPECTS, wrong_type  # noqa: E402

def parse_args(argv: list[str]) -> tuple[Path, int]:
    """(report path, how many trajectories to print).

    Positional arg is the report; `--trace N` asks for the step-by-step view.
    """
    path, trace = None, 0
    rest = list(argv)
    while rest:
        arg = rest.pop(0)
        if arg == "--trace":
            trace = int(rest.pop(0)) if rest and rest[0].isdigit() else 3
        elif arg.startswith("--trace="):
            trace = int(arg.split("=", 1)[1])
        elif not arg.startswith("-"):
            path = Path(arg)
    return path or (Path(__file__).parent / "template_run_report.json"), trace


REPORT, TRACE_N = parse_args(sys.argv[1:])


def main(report: Path = REPORT, trace_n: int = TRACE_N) -> int:
    sessions = json.loads(report.read_text())
    rows = [t for s in sessions for t in s["turns"] if t.get("input_tokens")]
    if not rows:
        print("no completed turns with usage in the report")
        return 1

    calls = [len(r["tool_calls"]) for r in rows]
    toks = [r["input_tokens"] for r in rows]
    median_calls = statistics.median(calls)

    print(f"{len(rows)} turns | calls: median {median_calls:.0f}, max {max(calls)} | "
          f"input tokens: total {sum(toks):,}, median {statistics.median(toks):,.0f}")

    # Which model produced these numbers, read off the run itself. Step counts
    # from a strong model and a weak one are not comparable, and a report that
    # does not say which it was invites exactly that comparison — the 33-step
    # metric baseline was a default-model run being read next to weak-model
    # follow-ups. Reports written before the runner recorded this say "not
    # recorded", which is honest; it is not the same as "the weak model".
    served = Counter(r.get("model") or "not recorded (pre-2026-08-12 report)" for r in rows)
    print("model: " + ", ".join(f"{m} x{n}" for m, n in served.most_common()))
    print()

    # --- harness steps vs product steps -------------------------------------
    # `claude -p` defers MCP tool schemas: the agent spends steps on ToolSearch
    # to fetch the definitions it wants. The product does not work that way —
    # streamlit-mcp hands the whole tool list to the model up front, paying the
    # fixed definition overhead OI-138 is about instead. So these step counts
    # are the harness's, not the product's, and the two rigs pay for tool
    # definitions in different currencies. It does not change which templates
    # are expensive relative to each other, but any absolute number lifted out
    # of here and quoted as a product cost is wrong.
    searches = sum(1 for r in rows for t in r["tool_calls"]
                   if t.lower().startswith("toolsearch"))
    all_calls = sum(len(r["tool_calls"]) for r in rows)
    if searches:
        print(f"NOTE: {searches} of {all_calls} steps ({searches / all_calls:.0%}) are "
              f"ToolSearch — the harness deferring MCP tool schemas. The product sends "
              f"tool definitions up front and never pays these steps.\n")

    # --- the outliers: where the agent had to hunt -------------------------
    print("WORST OFFENDERS — most agent steps (each step re-sends the whole context)")
    print(f"  {'calls':>5} {'in-tok':>11} {'sec':>6}  template")
    for r in sorted(rows, key=lambda x: -len(x["tool_calls"]))[:10]:
        print(f"  {len(r['tool_calls']):>5} {r['input_tokens']:>11,} "
              f"{r['seconds']:>6.0f}  {r['label']}")

    # cost of the tail, to size the prize
    heavy = [r for r in rows if len(r["tool_calls"]) > median_calls * 2]
    if heavy:
        saved = sum(r["input_tokens"] for r in heavy)
        print(f"\n  {len(heavy)} turns take more than double the median step count; "
              f"they burn {saved:,} tok, {saved / sum(toks):.0%} of the run.")

    # --- where the payload actually went -----------------------------------
    # Ranked by TOTAL chars, not by call count: one fat call outweighs many
    # small ones, and in a resumed session an early fat result is re-sent by
    # every later turn, so its true cost is size x remaining turns.
    sized = Counter()
    calls = Counter()
    for r in rows:
        for d in (r.get("calls_detail") or []):
            sized[d["tool"]] += d.get("result_chars", 0)
            calls[d["tool"]] += 1
    if sum(sized.values()):
        print("\nHEAVIEST PAYLOADS (total chars returned across the run)")
        print(f"  {'total':>9} {'calls':>6} {'avg':>8}  tool")
        for name, total in sized.most_common(8):
            n = calls[name]
            print(f"  {total:>9,} {n:>6} {total // max(n, 1):>8,}  {name}")

    # --- which tools they hunt with ---------------------------------------
    hunted = Counter(t for r in heavy for t in r["tool_calls"])
    if hunted:
        print("\n  tools those turns lean on (a repeated one usually means a "
              "description that did not answer the question):")
        for name, n in hunted.most_common(8):
            print(f"    {n:>3}x {name}")

    # --- the trajectory, step by step --------------------------------------
    # A tool histogram says "it called create_trait six times"; it cannot say
    # whether those were six different attempts at one thing or six deliberate
    # ones, and that difference is the whole diagnosis. The 33-step metric turn
    # was only understood once its calls were read in order with their
    # arguments: built correctly at step 3, then thirty steps of probing.
    if trace_n:
        print(f"\nTRAJECTORIES — the {trace_n} costliest turns, in order")
        for r in sorted(rows, key=lambda x: -len(x["tool_calls"]))[:trace_n]:
            detail = r.get("calls_detail") or []
            print(f"\n  {r['label']}  ({len(r['tool_calls'])} calls, "
                  f"{r['input_tokens']:,} in-tok, {r.get('model') or 'model not recorded'})")
            if not detail:
                print("    (no per-call detail — report predates calls_detail)")
                continue
            for i, d in enumerate(detail, 1):
                args = d.get("args", "")
                if len(args) > 150:
                    args = args[:150] + "…"
                mark = "ERR" if d.get("is_error") else "   "
                print(f"    {i:>3}. {mark} {d['tool']:<32} "
                      f"{d.get('result_chars', 0):>7,}ch  {args}")

    # --- did it actually work ---------------------------------------------
    # The wrong-type check is recomputed here rather than read from the report,
    # so reports written before the runner recorded it are checked too. The
    # category is the part of the label before " · ", which every report has.
    def mistyped(r):
        # Recompute in preference to reading the stored verdict: the rule is
        # still being sharpened, and a report written by an older runner carries
        # the older rule's answer. Recomputing applies today's rule to every
        # report, so two reports are never judged by two different rules. The
        # stored value is the fallback for reports that predate `named_present`.
        named = r.get("named_present")
        if named is None:
            return r.get("wrong_type")
        expect = CATEGORY_EXPECTS.get(r["label"].split(" · ")[0].strip())
        return wrong_type(expect, named)

    errs = [r for r in rows if r.get("errors")]
    missing = [r for r in rows if r.get("named_missing")]
    changed = [r for r in rows if r.get("changes")]
    typed_wrong = [(r, m) for r in rows if (m := mistyped(r))]
    print(f"\nOUTCOMES: {len(changed)}/{len(rows)} changed backend state, "
          f"{len(errs)} hit a tool error, {len(missing)} left a named resource absent, "
          f"{len(typed_wrong)} built the wrong kind of resource")
    for r in errs:
        first = (r["errors"][0] or "")[:120].replace("\n", " ")
        print(f"  ERROR  {r['label'][:44]:<44} {first}")
    for r in missing:
        print(f"  MISSING {r['label'][:43]:<43} {r['named_missing']}")
    for r, m in typed_wrong:
        print(f"  WRONG TYPE {r['label'][:40]:<40} {m}")

    # --- what got created, so create/edit/delete is visible ---------------
    made = Counter()
    for r in rows:
        for rtype, v in (r.get("changes") or {}).items():
            made[rtype] += len(v["created"])
    if made:
        print("\nRESOURCES CREATED: " +
              ", ".join(f"{k} {v}" for k, v in made.most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())

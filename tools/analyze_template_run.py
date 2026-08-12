#!/usr/bin/env python3
"""Turn a template run report into the list of things worth fixing.

Input tokens for a turn are roughly (number of model requests) x (context size),
and the context is re-sent on every step — so the cost of a template is driven by
how many steps the agent needs, not by how much data it moves. Measured on the
first session: tool results were 84k chars against 1.5M input tokens billed,
about 1.4%.

So the ranking that matters is steps per template, and the outliers are prompts
or tool descriptions that make the agent hunt.

    tools/analyze_template_run.py [report.json]
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

REPORT = Path(sys.argv[1] if len(sys.argv) > 1 else
              Path(__file__).parent / "template_run_report.json")


def main() -> int:
    sessions = json.loads(REPORT.read_text())
    rows = [t for s in sessions for t in s["turns"] if t.get("input_tokens")]
    if not rows:
        print("no completed turns with usage in the report")
        return 1

    calls = [len(r["tool_calls"]) for r in rows]
    toks = [r["input_tokens"] for r in rows]
    median_calls = statistics.median(calls)

    print(f"{len(rows)} turns | calls: median {median_calls:.0f}, max {max(calls)} | "
          f"input tokens: total {sum(toks):,}, median {statistics.median(toks):,.0f}")
    print()

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

    # --- did it actually work ---------------------------------------------
    errs = [r for r in rows if r.get("errors")]
    missing = [r for r in rows if r.get("named_missing")]
    changed = [r for r in rows if r.get("changes")]
    print(f"\nOUTCOMES: {len(changed)}/{len(rows)} changed backend state, "
          f"{len(errs)} hit a tool error, {len(missing)} left a named resource absent")
    for r in errs:
        first = (r["errors"][0] or "")[:120].replace("\n", " ")
        print(f"  ERROR  {r['label'][:44]:<44} {first}")
    for r in missing:
        print(f"  MISSING {r['label'][:43]:<43} {r['named_missing']}")

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

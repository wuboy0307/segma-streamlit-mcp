#!/usr/bin/env python3
"""Side-by-side of two template-run reports: steps per turn, and correctness.

The question OI-138 asks of the read-collapse switch is not "did the payload
shrink" — that is arithmetic, already known — but "did the weak model need more
steps to compose a path than to pick a typed tool". Steps are the unit that
costs; a saving of N tokens is worth N x steps, and one extra step costs more
than this saves in a short session.

Correctness is read off the recorded resource delta and the expectation checks,
never off the agent's own summary.

    tools/compare_template_runs.py BASE.json CANDIDATE.json \\
        [--tokens-base T --tokens-cand T]

Only turns that succeeded in BOTH arms are counted: a run that gives up early is
cheap, so comparing raw totals reads a failure as a saving.
"""
import json
import sys
from pathlib import Path


def turns(path):
    out = []
    for session in json.loads(Path(path).read_text()):
        for t in session["turns"]:
            out.append(t)
    return out


def _bad(row):
    """A turn is not comparable unless it actually did what was asked."""
    return bool(row["errors"] or row["is_error"]
                or row["named_missing"] or row["wrong_type"])


def summarise(t):
    calls = t.get("tool_calls") or []
    product = [c for c in calls if not c.lower().startswith("toolsearch")]
    return {
        "label": t["label"],
        "steps": len(calls),
        "product_calls": len(product),
        "tools": product,
        "input_tokens": t.get("input_tokens"),
        "errors": len(t.get("errors") or []),
        "is_error": bool(t.get("is_error")),
        "named_missing": t.get("named_missing") or [],
        "wrong_type": t.get("wrong_type") or [],
        "changes": t.get("changes") or {},
    }


def main():
    rest = list(sys.argv[1:])
    args, flags = [], {}
    while rest:
        a = rest.pop(0)
        if a.startswith("--"):
            flags[a.lstrip("-")] = rest.pop(0)
        else:
            args.append(a)
    if len(args) != 2:
        sys.exit(__doc__)

    a = [summarise(t) for t in turns(args[0])]
    b = [summarise(t) for t in turns(args[1])]
    if len(a) != len(b):
        print(f"!! different turn counts: {len(a)} vs {len(b)} — not comparable turn by turn")

    print(f"{'turn':<34} {'base':>6} {'cand':>6} {'delta':>6}   correctness")
    print("-" * 88)
    for x, y in zip(a, b):
        bad_x, bad_y = _bad(x), _bad(y)
        mark = ""
        if bad_x or bad_y:
            mark = f"base={'BAD' if bad_x else 'ok'} cand={'BAD' if bad_y else 'ok'}"
        print(f"{x['label'][:33]:<34} {x['steps']:>6} {y['steps']:>6} "
              f"{y['steps'] - x['steps']:>+6}   {mark}")
    sa, sb = sum(x["steps"] for x in a), sum(y["steps"] for y in b)
    print("-" * 88)
    print(f"{'TOTAL steps (all turns)':<34} {sa:>6} {sb:>6} {sb - sa:>+6}")

    # A turn that failed did not do the work, so its step count is not a cost of
    # doing the work — and a run that gives up early is CHEAP. Comparing totals
    # across arms therefore reads a failure as a saving, which is the one
    # direction this measurement gets misread. Only turns that succeeded in BOTH
    # arms are comparable; everything else is reported and excluded.
    pairs = [(x, y) for x, y in zip(a, b) if not _bad(x) and not _bad(y)]
    dropped = len(a) - len(pairs)
    ca = sum(x["steps"] for x, _ in pairs)
    cb = sum(y["steps"] for _, y in pairs)
    print(f"{'COMPARABLE steps':<34} {ca:>6} {cb:>6} {cb - ca:>+6}"
          f"   ({len(pairs)}/{len(a)} turns ok in both arms)")
    if dropped:
        print(f"   {dropped} turn(s) excluded — a turn that failed did not do the "
              f"work, so its steps are not a cost of doing it.")
    if not pairs:
        print("   NOTHING IS COMPARABLE — no turn succeeded in both arms.")
        return

    ia = sum(x["input_tokens"] or 0 for x in a)
    ib = sum(y["input_tokens"] or 0 for y in b)
    print(f"{'input tokens (claude -p rig)':<34} {ia:>6} {ib:>6} {ib - ia:>+6}")
    sa, sb = ca, cb

    tb, tc = flags.get("tokens-base"), flags.get("tokens-cand")
    if tb and tc:
        tb, tc = int(tb), int(tc)
        saved = (tb - tc)
        print()
        print(f"tools/list payload: base {tb:,} tok, candidate {tc:,} tok "
              f"→ {saved:,} tok saved per step")
        print("on the PRODUCT path (all tools sent every step), comparable turns only:")
        print(f"  base      = {tb:,} x {sa} steps = {tb * sa:,} tok")
        print(f"  candidate = {tc:,} x {sb} steps = {tc * sb:,} tok")
        print(f"  net       = {tc * sb - tb * sa:+,} tok "
              f"({'CHEAPER' if tc * sb < tb * sa else 'MORE EXPENSIVE'})")
        if sb > sa:
            # The break-even the handoff asks for, stated in this run's own terms
            # rather than as a remembered constant: the threshold moves with
            # session length, and treating it as a constant produced a wrong
            # conclusion once already.
            print(f"  the {sb - sa} extra step(s) cost {tc * (sb - sa):,} tok; "
                  f"the trim saves {saved * sa:,} over the base's {sa} steps")


if __name__ == "__main__":
    main()

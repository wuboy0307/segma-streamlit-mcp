#!/usr/bin/env python3
"""Move the app's prompt templates into segma-mcp, without changing one of them.

The templates are the product's own answer to "what do I type to build a CDP",
and today they exist only inside this Streamlit app — so an agent driving Segma
through MCP cannot see them. That is the MCP-parity gap: the browser has a
capability the agent path does not. Serving them from the server closes it for
every client, and costs nothing per model step, because MCP Prompts are fetched
when invoked rather than sent with every request the way tool definitions are.

This script is the migration, and it is mechanical on purpose. It reads
`PROMPT_TEMPLATES` out of streamlit_app.py with `ast` (no import, so Streamlit
need not be installed), writes the YAML segma-mcp serves, and then reads that
YAML back and asserts the round trip is exact: same categories in the same
order, same labels, same text, byte for byte, in both languages. A migration
that "looks right" is the failure mode here — these strings are what users are
told to type, and a silently dropped placeholder is a template that no longer
works.

    tools/extract_prompt_templates.py            # write + verify
    tools/extract_prompt_templates.py --check    # verify only, change nothing
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
import unicodedata
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "streamlit_app.py"
OUT = REPO.parent / "segma-mcp" / "prompts" / "templates.yaml"

# The category keys carry an emoji and the display name in each language; the id
# is what a Prompt is named after, so it has to be stable, ASCII and readable.
# The second value is the MCP Prompt name — it is what a user types as a slash
# command (`/mcp__segma__build_trait`), so it reads as an action rather than as
# a noun, and it lives here rather than in server.py so there is one list, not
# two that can drift apart.
CATEGORY_IDS = {
    "🔌 Connect Data": ("connect_data", "connect_data"),
    "👤 Entity (what you analyze)": ("entity", "build_entity"),
    "📅 Event (what happens)": ("event", "build_event"),
    "📊 Metric (a computed number)": ("metric", "build_metric"),
    "🏷️ Trait (attributes / computed values)": ("trait", "build_trait"),
    "🎯 Segment (a group)": ("segment", "build_segment"),
    "📦 Action Dataset (output columns)": ("action_dataset", "build_action_dataset"),
    "📮 Destination (where to export)": ("destination", "setup_destination"),
    "🔄 Sync (send it out)": ("sync", "run_sync"),
    "🗃️ Feature Store (real-time lookup)": ("feature_store", "setup_feature_store"),
    "🔍 Query / Explore (see what's built)": ("explore", "explore_segma"),
    "🚀 One-shot full flow": ("full_flow", "build_full_cdp"),
}


def read_templates() -> dict:
    """PROMPT_TEMPLATES as data, without importing the app."""
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "PROMPT_TEMPLATES" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    sys.exit("PROMPT_TEMPLATES not found in streamlit_app.py")


def slug(label: str) -> str:
    """A stable id for one template inside its category.

    Labels are Chinese in one language and English in the other, and the id has
    to be the same in both, so it is derived from the ENGLISH label only. Any
    non-ASCII survivor would make the id depend on the reader's locale.
    """
    s = unicodedata.normalize("NFKD", label)
    s = s.encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "item"


def build(templates: dict) -> dict:
    zh, en = templates["zh"], templates["en"]
    zh_cats, en_cats = list(zh), list(en)
    if len(zh_cats) != len(en_cats):
        sys.exit(f"category count differs: zh={len(zh_cats)} en={len(en_cats)}")

    out_cats = []
    for zh_cat, en_cat in zip(zh_cats, en_cats):
        mapped = CATEGORY_IDS.get(en_cat)
        if not mapped:
            sys.exit(f"no id mapped for category {en_cat!r} — add it to CATEGORY_IDS")
        cat_id, prompt_name = mapped
        zh_items, en_items = zh[zh_cat], en[en_cat]
        if len(zh_items) != len(en_items):
            sys.exit(f"{cat_id}: zh has {len(zh_items)} templates, en has {len(en_items)}")

        seen: dict[str, int] = {}
        items = []
        for (zh_label, zh_text), (en_label, en_text) in zip(zh_items, en_items):
            base = slug(en_label)
            seen[base] = seen.get(base, 0) + 1
            item_id = base if seen[base] == 1 else f"{base}_{seen[base]}"
            items.append({
                "id": item_id,
                "zh": {"label": zh_label, "text": zh_text},
                "en": {"label": en_label, "text": en_text},
            })
        out_cats.append({
            "id": cat_id,
            "prompt_name": prompt_name,
            "zh": {"name": zh_cat},
            "en": {"name": en_cat},
            "templates": items,
        })
    return {"categories": out_cats}


def flatten(doc: dict) -> list[tuple]:
    """Every (category name, label, text) pair, in order, for both languages.

    This is the invariant the round trip is checked against: the migration may
    reorganise the container, never the content.
    """
    rows = []
    for cat in doc["categories"]:
        for lang in ("zh", "en"):
            for item in cat["templates"]:
                rows.append((lang, cat[lang]["name"], item[lang]["label"], item[lang]["text"]))
    return rows


def flatten_source(templates: dict) -> list[tuple]:
    rows = []
    zh_cats, en_cats = list(templates["zh"]), list(templates["en"])
    for zh_cat, en_cat in zip(zh_cats, en_cats):
        for lang, cat in (("zh", zh_cat), ("en", en_cat)):
            for label, text in templates[lang][cat]:
                rows.append((lang, cat, label, text))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="verify the committed YAML still matches the app, write nothing")
    ap.add_argument("--check-doc", action="store_true",
                    help="verify only PROMPTS.md against the app; needs neither "
                         "PyYAML nor the segma-mcp checkout, so CI can run it")
    args = ap.parse_args()

    # Returns before `import yaml` and before anything reads OUT, on purpose.
    # This half is the only one CI can run: the YAML being checked lives in
    # segma-mcp, a different repository on a different host, and a CI job here
    # checks out this repo alone. Keeping the doc check free of both
    # dependencies is what lets it be a real gate rather than a skip.
    if args.check_doc:
        return check_prompts_md(read_templates())

    import yaml

    templates = read_templates()
    doc = build(templates)

    want = flatten_source(templates)
    # Compare against the order the YAML actually groups by, not the app's.
    want_sorted = sorted(want)

    if args.check:
        if not OUT.exists():
            sys.exit(f"{OUT} does not exist — run without --check first")
        doc = yaml.safe_load(OUT.read_text(encoding="utf-8"))
    else:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(
            "# GENERATED — do not edit by hand.\n"
            "# Source: segma-streamlit-mcp/streamlit_app.py PROMPT_TEMPLATES,\n"
            "# via tools/extract_prompt_templates.py. Edit the app, then re-run it;\n"
            "# tests/test_prompt_templates_sync.py fails if the two drift.\n"
            + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=10**6),
            encoding="utf-8",
        )
        doc = yaml.safe_load(OUT.read_text(encoding="utf-8"))

    got_sorted = sorted(flatten(doc))
    if got_sorted != want_sorted:
        missing = [r for r in want_sorted if r not in got_sorted]
        extra = [r for r in got_sorted if r not in want_sorted]
        print(f"ROUND TRIP FAILED: {len(missing)} missing, {len(extra)} unexpected")
        for r in (missing + extra)[:5]:
            print("   ", r[0], r[1][:20], "|", r[2][:30], "|", r[3][:60])
        return 1

    n = sum(len(c["templates"]) for c in doc["categories"])
    print(f"{len(doc['categories'])} categories, {n} templates x 2 languages "
          f"= {len(got_sorted)} strings — round trip exact")
    print(f"{'checked' if args.check else 'wrote'} {OUT}")

    # Two exit codes, because the two modes are asking different questions and
    # a caller reacts differently. Under --check a stale PROMPTS.md is a
    # FAILURE: the committed state is inconsistent. In write mode it is the
    # EXPECTED half-way point — you have just added a template to the app and
    # the doc cannot possibly know about it yet — so failing with 1 there would
    # break `extract_prompt_templates.py && git add …` on the one run that is
    # supposed to work, and teach people to ignore the message. The YAML was
    # written either way; only the doc is outstanding.
    doc = check_prompts_md(templates)
    if doc and not args.check:
        print("^ the YAML above was written correctly; this is the remaining "
              "step, not a failed generation (exit 2).")
        return 2
    return doc


# PROMPTS.md is the third copy of these strings — the human-readable one, and
# the only one nothing generates. It is checked HERE rather than only in
# tests/test_prompt_templates_sync.py because this script is the step the
# workflow actually names: adding a template means editing the app and running
# this, so this is the moment the omission can still be caught. Measured
# 2026-08-28, before this existed: 11 templates missing from the doc and 5
# whose text had been left at an older wording — including the whole sync
# dedup / sort / retry batch added on 2026-08-19, where the YAML was
# regenerated and the doc was not.
BLOCK_RE = re.compile(r"^\*\*([^\n*]+)\*\*\n```\n(.*?)\n```", re.M | re.S)
HEADING_RE = re.compile(r"^## (.+)$", re.M)


def blocks_by_section(text: str):
    """(heading, label, body) for every template block, with its `## ` section.

    The heading is what makes a label unique, so it has to come out of the
    document rather than be assumed from order.
    """
    heads = [(m.start(), m.group(1)) for m in HEADING_RE.finditer(text)]
    for i, (pos, heading) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(text)
        for m in BLOCK_RE.finditer(text[pos:end]):
            yield heading, m.group(1), m.group(2)


def check_prompts_md(templates: dict) -> int:
    """Every zh template appears in PROMPTS.md, in its own section, same text."""
    doc_path = REPO / "PROMPTS.md"
    if not doc_path.exists():
        print(f"{doc_path} is missing")
        return 1

    # Keyed by (category, label), not by label alone. A label is only unique
    # WITHIN its category — `Follow the source (follow_refresh)` is already
    # carried by both `sync` and `feature_store`, identically, which is why
    # their generated ids collide too — so flattening on the label would refuse
    # a catalogue that is perfectly legal, and would do it the moment somebody
    # tidied those two zh labels into the same wording. Duplicates are still
    # rejected within one category, where they really would collapse a dict and
    # let the check compare fewer things than it reports.
    app = {}
    for cat, items in templates["zh"].items():
        for label, text in items:
            if (cat, label) in app:
                print(f"two templates in '{cat}' share the label {label!r}, so "
                      "PROMPTS.md cannot be matched against them one for one. "
                      "Rename one.")
                return 1
            app[(cat, label)] = text

    md = {}
    text = doc_path.read_text(encoding="utf-8")
    for heading, block_label, block_text in blocks_by_section(text):
        if (heading, block_label) in md:
            print(f"PROMPTS.md carries the heading {block_label!r} twice under "
                  f"'## {heading}'.")
            return 1
        md[(heading, block_label)] = block_text

    missing = [l for l in app if l not in md]
    extra = [l for l in md if l not in app]
    differs = [l for l in md if l in app and md[l].strip() != app[l].strip()]

    if not (missing or extra or differs):
        print(f"PROMPTS.md carries all {len(app)} templates, text identical")
        return 0

    print(f"PROMPTS.md is out of step with streamlit_app.py "
          f"({len(missing)} missing, {len(extra)} unknown, {len(differs)} reworded).")
    print("It is written by hand — add the block under the matching `## ` heading, "
          "or copy the new text into the existing one.")
    for kind, rows in (("missing ", missing), ("unknown ", extra), ("reworded", differs)):
        for cat, label in rows:
            print(f"   {kind}: {label}   (## {cat})")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

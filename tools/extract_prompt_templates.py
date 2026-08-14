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
    args = ap.parse_args()

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""What kind of resource each prompt-template category is supposed to produce.

Its own module, with no third-party imports, because both the runner (which
needs httpx) and the analyzer (which is meant to stay runnable with a bare
`python3`) apply the same rule. Copying the table into both is what makes two
copies disagree later.
"""
from __future__ import annotations

# Taken from the category label the turns files already carry, so no turns file
# needs editing.
#
# Why this is checked at all: a name is looked up across EVERY resource type, so
# building the right name as the wrong type reads as success. That is not
# hypothetical — on 2026-08-12 the 〔聚合〕消費次數 = COUNT template, whose prompt
# says 建聚合標籤 (an aggregation TRAIT), produced a metric named `b_消費次數`,
# and the run reported `ok`. Trait and metric are different resources with
# different SQL; a later turn asking for that trait as an output column is
# building on something that is not there.
#
# Every category a turns file uses must appear here, `None` for the read-only
# ones. An unlisted category would simply skip the check — silently, and looking
# exactly like a pass — so `check_categories_mapped()` asserts the coverage
# instead of trusting that this dict kept up.
CATEGORY_EXPECTS: dict[str, str | None] = {
    "🔌 連接資料": "data_sources",
    "👤 分析主體(要分析的對象)": "dims",
    "📅 事件(發生的事)": "facts",
    "📊 指標(可計算的數字)": "metrics",
    "🏷️ 標籤(對象的屬性 / 計算結果)": "traits",
    "🎯 分群(篩出一群對象)": "segments",
    "📦 行動資料(整理輸出欄位)": "action_datasets",
    "📮 同步目的地(要匯出去哪)": "destinations",
    "🔄 同步(把名單送出去)": "syncs",
    "🗃️ 特徵商店(即時查特徵)": "feature_stores",
    # Reads only — asks what exists, builds nothing.
    "🔍 查詢 / 探索(看看建了什麼)": None,
}


def expected_type(raw_item: dict) -> str | None:
    """The resource type this turn's category says it should produce."""
    template = raw_item.get("template") or []
    return CATEGORY_EXPECTS.get(template[0]) if template else None


def wrong_type(expect: str | None, named: dict) -> str | None:
    """Complaint if every one of the turn's names exists, but none as `expect`.

    `named` is {resource name: ["dims#120", …]} — the report's `named_present`.

    Two deliberate narrowings, both to keep this saying one thing:

    "at least one", not "all" — a turn's `fill` also NAMES the resources it
    builds ON (the action-dataset turn lists the traits it outputs), and those
    legitimately belong to other types. The question is only whether the thing
    this template exists to create came out as that kind of thing.

    Silent when any name is missing — then the resource was not built at all,
    which the MISSING check already reports. Firing here too restated one fact
    as two complaints, and the interesting case is precisely the other one: the
    name IS there, under the wrong type, so nothing else notices.
    """
    if not expect or not named:
        return None
    if any(not hits for hits in named.values()):
        return None
    found = {t.split("#")[0] for hits in named.values() for t in hits}
    if not found or expect in found:
        return None
    return f"expected a {expect} record; the names resolve to {sorted(found)}"


def check_categories_mapped(raw_turns: list) -> list[str]:
    """Categories in this turns file that CATEGORY_EXPECTS does not cover."""
    return sorted({
        x["template"][0] for x in raw_turns
        if isinstance(x, dict) and x.get("template")
        and x["template"][0] not in CATEGORY_EXPECTS
    })

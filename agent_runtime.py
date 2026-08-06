"""
Agent runtime — 純邏輯,不碰 Streamlit,方便單元測試。

streamlit_app.py 負責 UI 與快取;真正「怎麼建 agent、怎麼串流跑一輪、哪些工具算
破壞性要先確認」的邏輯都放這裡,這樣 tests/ 可以用 pydantic-ai 的 TestModel 直接
驗證,不用起 Streamlit、不用連真的 LLM / MCP。

三件事:
- build_agent():把 MCP 工具接上 LLM,破壞性工具用 ApprovalRequiredToolset 包起來。
- stream_turn():用 agent.iter 跑一輪,工具呼叫 / 結果 / 文字**邊跑邊**用 callback 吐出去。
- is_destructive() / needs_approval():哪些工具要先請使用者按確認才放行。
"""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Any, Awaitable, Callable, Optional

from pydantic_ai import Agent, ApprovalRequiredToolset, DeferredToolRequests
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

# 破壞性 / 會真的動到資料的工具:刪除、批次刪除、觸發(跑同步 / 跑行動資料 = 真的送 /
# 真的建表)、重建容器(砍掉重來)。這些在 agent 呼叫前會被攔下來請使用者按確認。
# 只用前綴比對——segma-mcp 的工具名是 backend swagger operationId 自動生成的,一律是
# `<動詞>_<資源>` 格式(delete_segment / trigger_sync / batch_destroy_action_datasets…)。
DESTRUCTIVE_PREFIXES = (
    "delete_",
    "batch_destroy_",
    "trigger_",
    "dev_recreate_",
    "prod_recreate_",
)


def is_destructive(tool_name: str) -> bool:
    """這個工具會不會不可逆地動到資料 / 真的把資料送出去?"""
    return tool_name.startswith(DESTRUCTIVE_PREFIXES)


def _approval_required(ctx: Any, tool_def: Any, tool_args: dict[str, Any]) -> bool:
    return is_destructive(tool_def.name)


# ---------------------------------------------------------------------------
# 工具定義的送出成本:哪些一開始就給 model 看,哪些等它自己找
# ---------------------------------------------------------------------------
#
# segma-mcp 目前有 152 個工具,整包工具定義是 49,908 tok,而工具定義是**每一輪都重送**
# 的。這裡走 OpenAI API,是真的計費的那條路(本機 Claude Code 走 Tool Search,不付這筆)。
#
# 關鍵區別:這是**延後**,不是過濾。
#   - 過濾(`.filtered()`)會把工具從 agent 手上拿掉 → 少一個能力。
#   - 延後(`defer_loading=True`)只改「schema 什麼時候送」→ model 先看到一份精簡清單,
#     需要別的就透過 tool search 自己撈進來。能力一個都沒少。
# 所以下面這份清單寫漏了不會壞掉,只是多花一輪去搜;寫太多才是白付 token。
# 這也是為什麼「不在清單裡」是預設:server 之後新增的工具會自動變成延後載入,
# 而不是自動變成每一輪都送。
#
# 清單怎麼來的(不是猜的):對 PROMPTS.md + streamlit_app.py 的提示詞、segma-mcp 的
# demos/build-workflow.md、以及 examples/ 底下所有實錄 transcript 取聯集——也就是
# 「文件明確叫 agent 用的」加上「真的被用過的」。
#
# **只算正面提及。** 第一版是「提示詞裡出現過工具名」就收進來,結果把 `get_profile`
# 收了進來——它在提示詞裡唯一的出現是「別用 get_profile 之類的工具亂湊」,是被禁止的。
# 六次真實 gpt-4o 跑測中它被呼叫 0 次。要每輪付 schema 的錢,理由必須是「提示詞叫它
# 用」,不是「提示詞提到它」。
#
# 效果:eager 35 個 = 27,162 tok;延後 117 個 = **每一輪省 22,746 tok(45.6%)**。
#
# 用 SEGMA_EAGER_TOOLS 覆寫(逗號分隔);設成 `none` 表示全部延後(最省,但每種資源
# 第一次用都要多一輪搜尋)。
EAGER_TOOLS = frozenset({
    # 建立各資源 —— 建 CDP 的主線
    "create_data_source", "create_dim", "create_fact", "create_trait",
    "create_metric", "create_segment", "create_action_dataset",
    "create_feature_store", "create_destination", "create_sync",
    # 盤點與 name→id 解析
    "list_data_sources", "list_data_source_columns", "list_dims", "list_facts",
    "list_traits", "list_metrics", "list_segments", "list_action_datasets",
    "list_action_dataset_columns", "list_feature_stores", "list_destinations",
    "list_syncs", "list_sync_run_history",
    "search_traits", "show_trait",
    # 可用函式清單 —— 建 trait / metric 前要先查
    "list_aggr_functions", "list_compute_functions", "list_db_functions",
    # 看資料與驗證結果。
    # 沒有 get_profile:提示詞明文叫 agent 不要用它來盤點(要用對應的 list_*),
    # 而且它的回應會隨使用者擁有的資源無上限成長(本機實測 9,368 tok,其中 399 個
    # trait 佔 68.8%)。需要時它仍然搜得到。
    "get_segment_data", "get_trait_data", "get_action_dataset_data",
    "get_feature_store_data",
    # schema 重抓與啟動同步
    "refresh_data_source_schema", "trigger_sync",
})


def eager_tools() -> frozenset[str] | None:
    """哪些工具一開始就送出完整 schema。None = 全部延後。"""
    raw = os.environ.get("SEGMA_EAGER_TOOLS", "").strip()
    if not raw:
        return EAGER_TOOLS
    if raw.lower() == "none":
        return None
    return frozenset(n.strip() for n in raw.split(",") if n.strip())


def _defer_non_eager(eager: frozenset[str] | None):
    """回傳一個 prepare function,把不在 eager 清單裡的工具標成延後載入。

    用 `.prepared()` 而不是 `.defer_loading(names)`,因為後者要「列出要延後的」,
    而工具清單是 MCP server 開機時才知道的、還會長。反過來列 eager 的,新工具就
    自動落在延後那邊——這是安全的方向。
    """
    async def prepare(_ctx: Any, tool_defs: list[Any]) -> list[Any]:
        if eager is None:
            return [replace(td, defer_loading=True) for td in tool_defs]
        return [
            td if td.name in eager else replace(td, defer_loading=True)
            for td in tool_defs
        ]

    return prepare


def build_agent(
    *,
    mcp_url: str,
    token: str,
    model_name: str,
    api_key: str,
    base_url: str,
    verify: bool,
    max_tokens: int,
    instructions: str,
    require_confirm: bool = True,
    temperature: float = 0.0,
    seed: int | None = None,
) -> Agent:
    """接好一個 agent。require_confirm=True 時破壞性工具走人工核准流程。

    temperature 預設 0:這是個「照工具建 CDP」的 agent,要的是可重現、每次一樣的動作,
    不是有創意的措辭,所以低溫最合適。gen_transcript 另外傳固定 seed,讓範例可重現。"""
    toolset = MCPToolset(mcp_url, headers={"Authorization": f"Bearer {token}"}, verify=verify)
    # 只有 eager 清單裡的工具一開始就送 schema,其餘標成延後載入,由 tool search 撈。
    # 這一步放在 approval 包裝**之前**:延後只影響「schema 何時送」,呼叫時仍然會經過
    # ApprovalRequiredToolset,所以事後才被搜出來的破壞性工具照樣要按確認。
    toolset = toolset.prepared(_defer_non_eager(eager_tools()))
    provider_kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        provider_kwargs["base_url"] = base_url
    model = OpenAIChatModel(model_name, provider=OpenAIProvider(**provider_kwargs))

    if require_confirm:
        # 破壞性工具被呼叫時 raise ApprovalRequired → 這一輪以 DeferredToolRequests
        # 收尾,交回 app 顯示確認 UI。所以 output_type 要含 DeferredToolRequests。
        toolsets = [ApprovalRequiredToolset(toolset, _approval_required)]
        output_type: Any = [str, DeferredToolRequests]
    else:
        toolsets = [toolset]
        output_type = str

    # retries=5:巢狀工具(create_fact 的 join…)第一次常填不對,靠讀錯誤自我修正;
    # pydantic-ai 預設 tool retry 上限 1,一被擋整個 run 就崩,所以放寬。
    settings: dict[str, Any] = {"max_tokens": max_tokens, "temperature": temperature}
    if seed is not None:
        settings["seed"] = seed
    return Agent(
        model,
        toolsets=toolsets,
        output_type=output_type,
        instructions=instructions,
        retries=5,
        model_settings=settings,
    )


ToolCallCb = Callable[[Any], None]        # 收到 ToolCallPart
ToolResultCb = Callable[[Any], None]      # 收到 ToolReturnPart
TextCb = Callable[[str, bool], None]      # (chunk, replace):replace=新的一段文字開頭


async def stream_turn(
    agent: Agent,
    *,
    prompt: Optional[str] = None,
    message_history: Optional[list] = None,
    deferred_results: Any = None,
    on_tool_call: Optional[ToolCallCb] = None,
    on_tool_result: Optional[ToolResultCb] = None,
    on_text: Optional[TextCb] = None,
):
    """
    用 agent.iter 跑一輪,邊跑邊把事件用 callback 吐出去,回傳最終 run.result。

    - prompt=None + deferred_results=... → 這是「使用者按完確認」後的續跑。
    - on_tool_call(part):工具開始被呼叫(part 是 ToolCallPart,有 tool_name / args / tool_call_id)。
    - on_tool_result(part):工具回來了(part 是 ToolReturnPart,有 tool_call_id / content)。
    - on_text(chunk, replace):助手文字串流;replace=True 代表新一段文字的開頭。
    """
    kwargs: dict[str, Any] = {}
    if message_history is not None:
        kwargs["message_history"] = message_history
    if deferred_results is not None:
        kwargs["deferred_tool_results"] = deferred_results

    async with agent.iter(prompt, **kwargs) as run:
        async for node in run:
            if Agent.is_call_tools_node(node):
                async with node.stream(run.ctx) as stream:
                    async for event in stream:
                        if isinstance(event, FunctionToolCallEvent) and on_tool_call:
                            on_tool_call(event.part)
                        elif isinstance(event, FunctionToolResultEvent) and on_tool_result:
                            on_tool_result(event.part)
            elif Agent.is_model_request_node(node) and on_text:
                async with node.stream(run.ctx) as stream:
                    async for event in stream:
                        if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                            if event.part.content:
                                on_text(event.part.content, True)
                        elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                            if event.delta.content_delta:
                                on_text(event.delta.content_delta, False)
    return run.result

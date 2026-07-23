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
) -> Agent:
    """接好一個 agent。require_confirm=True 時破壞性工具走人工核准流程。"""
    toolset = MCPToolset(mcp_url, headers={"Authorization": f"Bearer {token}"}, verify=verify)
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
    return Agent(
        model,
        toolsets=toolsets,
        output_type=output_type,
        instructions=instructions,
        retries=5,
        model_settings={"max_tokens": max_tokens},
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

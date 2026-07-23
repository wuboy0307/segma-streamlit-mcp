"""
agent_runtime 的單元測試 —— 不連真的 LLM / MCP,用 pydantic-ai 的 FunctionModel
(一個可程式化的假 model)+ FunctionToolset(本地假工具)驗證:

1. is_destructive:破壞性工具前綴分類正確。
2. stream_turn:工具呼叫 / 結果 / 文字**確實邊跑邊**透過 callback 吐出來,順序正確。
3. 確認閘門(require_confirm=True):破壞性工具會被攔成 DeferredToolRequests;
   - 拒絕(ToolDenied)→ 續跑,工具沒真的執行;
   - 核准(True)→ 續跑,工具真的執行了。

跑法:
    .venv/bin/python -m pytest tests/ -v
    .venv/bin/python tests/test_agent_runtime.py     # 不裝 pytest 也能跑
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic_ai import Agent, ApprovalRequiredToolset, DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import FunctionToolset

from agent_runtime import is_destructive, stream_turn

# 真的被執行到的破壞性工具會往這裡記一筆,用來驗證「拒絕 = 沒跑 / 核准 = 有跑」。
EXECUTED: list[str] = []


def create_dim(name: str) -> str:            # 安全工具(非破壞性前綴)
    return f"created dim {name}"


def delete_dim(dim_id: int) -> str:          # 破壞性工具(delete_ 前綴)
    EXECUTED.append(f"delete_dim:{dim_id}")
    return f"deleted dim {dim_id}"


def _make_agent(require_confirm: bool) -> Agent:
    """一個有 create_dim(安全)+ delete_dim(破壞性)兩個假工具的 agent。"""
    ts = FunctionToolset()
    ts.add_function(create_dim)
    ts.add_function(delete_dim)

    def model_func(messages: list, info: AgentInfo) -> ModelResponse:
        # 第一次:同時叫一個安全工具 + 一個破壞性工具。之後:收尾講句話。
        already_called = any(
            isinstance(p, ToolCallPart)
            for m in messages if isinstance(m, ModelResponse)
            for p in m.parts
        )
        if not already_called:
            return ModelResponse(parts=[
                ToolCallPart(tool_name="create_dim", args={"name": "Customer"}),
                ToolCallPart(tool_name="delete_dim", args={"dim_id": 7}),
            ])
        return ModelResponse(parts=[TextPart(content="全部完成了。")])

    toolset = ApprovalRequiredToolset(ts, lambda ctx, td, args: is_destructive(td.name)) if require_confirm else ts
    output_type = [str, DeferredToolRequests] if require_confirm else str
    return Agent(FunctionModel(model_func), toolsets=[toolset], output_type=output_type)


def _make_streaming_agent() -> Agent:
    """給 streaming 測試用:TestModel 原生支援串流(FunctionModel 不設 stream_function
    無法串流)。TestModel 會把每個工具各叫一次、再吐 custom_output_text。"""
    ts = FunctionToolset()
    ts.add_function(create_dim)
    ts.add_function(delete_dim)
    return Agent(TestModel(custom_output_text="全部完成了。"), toolsets=[ts])


# --------------------------------------------------------------------------- #

def test_is_destructive():
    for name in ("delete_segment", "batch_destroy_action_datasets", "trigger_sync",
                 "trigger_action_dataset", "dev_recreate_stream_airflow", "prod_recreate_stream_streamlit_app"):
        assert is_destructive(name), name
    for name in ("create_dim", "list_segments", "show_metric", "update_trait",
                 "get_segment_data", "refresh_data_source_schema", "dev_start_stream_airflow"):
        assert not is_destructive(name), name


def test_stream_turn_emits_live_events():
    """工具呼叫 / 結果 / 文字都應該透過 callback 即時吐出,且每個呼叫都有對應結果。"""
    agent = _make_streaming_agent()
    calls, results, texts = [], [], []

    async def run():
        return await stream_turn(
            agent, prompt="建個客戶維度然後刪掉舊的",
            on_tool_call=lambda p: calls.append(p.tool_name),
            on_tool_result=lambda p: results.append(p.tool_call_id),
            on_text=lambda chunk, replace: texts.append(chunk),
        )

    result = asyncio.run(run())
    assert calls == ["create_dim", "delete_dim"], calls          # 順序 = 呼叫順序
    assert len(results) == 2                                      # 兩個呼叫都拿到結果
    assert "".join(texts) == "全部完成了。"                        # 文字有串流出來
    assert result.output == "全部完成了。"


def test_destructive_tool_is_gated_and_denied():
    """破壞性工具被攔成 DeferredToolRequests;安全工具照跑;拒絕後不執行。"""
    EXECUTED.clear()
    agent = _make_agent(require_confirm=True)

    first = asyncio.run(stream_turn(agent, prompt="建個維度然後刪掉舊的"))
    assert isinstance(first.output, DeferredToolRequests)
    gated = {c.tool_name for c in first.output.approvals}
    assert gated == {"delete_dim"}                                # 只有破壞性的被攔
    assert EXECUTED == []                                         # 攔住時還沒執行

    dtr = DeferredToolResults()
    for c in first.output.approvals:
        dtr.approvals[c.tool_call_id] = ToolDenied("使用者拒絕")
    second = asyncio.run(stream_turn(agent, message_history=first.all_messages(), deferred_results=dtr))
    assert not isinstance(second.output, DeferredToolRequests)    # 解掉了,收尾
    assert EXECUTED == []                                         # 拒絕 → 真的沒執行


def test_destructive_tool_approved_executes():
    """核准後,破壞性工具真的被執行。"""
    EXECUTED.clear()
    agent = _make_agent(require_confirm=True)

    first = asyncio.run(stream_turn(agent, prompt="建個維度然後刪掉舊的"))
    assert isinstance(first.output, DeferredToolRequests)

    dtr = DeferredToolResults()
    for c in first.output.approvals:
        dtr.approvals[c.tool_call_id] = True                     # 核准
    second = asyncio.run(stream_turn(agent, message_history=first.all_messages(), deferred_results=dtr))
    assert not isinstance(second.output, DeferredToolRequests)
    assert EXECUTED == ["delete_dim:7"]                          # 核准 → 真的執行了


if __name__ == "__main__":
    for fn in [test_is_destructive, test_stream_turn_emits_live_events,
               test_destructive_tool_is_gated_and_denied, test_destructive_tool_approved_executes]:
        fn()
        print(f"  ✓ {fn.__name__}")
    print("all passed")

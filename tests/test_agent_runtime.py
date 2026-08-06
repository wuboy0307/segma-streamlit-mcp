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
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import FunctionToolset

from agent_runtime import (
    EAGER_TOOLS,
    _defer_non_eager,
    eager_tools,
    is_destructive,
    stream_turn,
)

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


# --------------------------------------------------------------------------- #
# 工具定義延後載入 —— 每一輪都重送的那 49,908 tok
# --------------------------------------------------------------------------- #

def _tool_defs(eager):
    """跑一次 prepare function,拿回它產出的 ToolDefinition。"""
    defs = [ToolDefinition(name=n) for n in
            ("create_dim", "list_dims", "delete_dim", "batch_destroy_dims", "update_dim")]
    return {td.name: td for td in asyncio.run(_defer_non_eager(eager)(None, defs))}


def test_eager_tools_are_sent_up_front_and_the_rest_deferred():
    out = _tool_defs(frozenset({"create_dim", "list_dims"}))
    assert out["create_dim"].defer_loading is False
    assert out["list_dims"].defer_loading is False
    # 沒列到的一律延後——包含 server 之後新增的工具,這是安全的預設方向。
    assert out["update_dim"].defer_loading is True
    assert out["delete_dim"].defer_loading is True


def test_none_defers_everything():
    out = _tool_defs(None)
    assert all(td.defer_loading is True for td in out.values())


def test_deferring_removes_no_tool():
    """延後 ≠ 過濾:工具還在清單裡,只是 schema 晚點送。"""
    out = _tool_defs(frozenset({"create_dim"}))
    assert set(out) == {"create_dim", "list_dims", "delete_dim",
                        "batch_destroy_dims", "update_dim"}


def test_eager_tools_env_override(monkeypatch=None):
    import os
    original = os.environ.get("SEGMA_EAGER_TOOLS")
    try:
        os.environ.pop("SEGMA_EAGER_TOOLS", None)
        assert eager_tools() == EAGER_TOOLS                  # 預設用內建清單
        os.environ["SEGMA_EAGER_TOOLS"] = "create_dim, list_dims"
        assert eager_tools() == frozenset({"create_dim", "list_dims"})
        os.environ["SEGMA_EAGER_TOOLS"] = "NONE"
        assert eager_tools() is None                          # 全部延後
    finally:
        os.environ.pop("SEGMA_EAGER_TOOLS", None)
        if original is not None:
            os.environ["SEGMA_EAGER_TOOLS"] = original


def test_every_eager_tool_is_non_destructive():
    """eager 清單不該包含破壞性工具:那些本來就要人工確認、不必為它們每輪付 token。

    trigger_sync 是刻意的例外——它是 `trigger_` 前綴(破壞性、要確認),但『建完就跑
    一次同步』是主線流程的一部分,提示詞也明確叫 agent 用它。"""
    unexpected = {n for n in EAGER_TOOLS if is_destructive(n)} - {"trigger_sync"}
    assert unexpected == set(), unexpected


def test_deferred_schemas_never_reach_the_wire():
    """這條才是真正的主張:延後的工具 schema **沒有被送出去**。

    前面幾條驗的是 defer_loading 旗標。旗標對、但 request 裡照樣夾著全部 152 份
    schema 的話,一個 token 都沒省——這正是第一版寫錯的地方:`AgentInfo.function_tools`
    是序列化**之前**的清單,兩種情況下都是全部,拿它斷言會誤報成功。

    所以這裡攔真正的 HTTP request body(MockTransport,不打 API、不花錢),數
    `tools[]` 裡真的有幾個。實測:40 個工具、延後 37 個 → 送出 4 個(3 個 eager
    加一個 search_tools),payload 小 86.4%。

    這條也是 pydantic-ai 升版的守衛:哪天它不再認 defer_loading,這裡會紅。
    """
    import json

    import httpx
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "gpt-4o",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    def build(eager):
        ts = FunctionToolset()
        ts.add_function(create_dim)
        ts.add_function(delete_dim)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        model = OpenAIChatModel(
            "gpt-4o", provider=OpenAIProvider(api_key="sk-test", http_client=client)
        )
        return Agent(model, toolsets=[ts.prepared(_defer_non_eager(eager))])

    captured.clear()
    asyncio.run(build(frozenset({"create_dim", "delete_dim"})).run("hi"))
    sent_all = {t["function"]["name"] for t in captured[0].get("tools", [])}

    captured.clear()
    asyncio.run(build(frozenset({"create_dim"})).run("hi"))
    sent_eager = {t["function"]["name"] for t in captured[0].get("tools", [])}

    assert {"create_dim", "delete_dim"} <= sent_all, sent_all
    assert "create_dim" in sent_eager
    assert "delete_dim" not in sent_eager, (
        f"延後的工具還是被送出去了,等於沒省到:{sent_eager}"
    )
    # 換來的是一個搜尋工具,讓被延後的仍然找得到——延後不是移除。
    assert "search_tools" in sent_eager, sent_eager


def test_a_deferred_destructive_tool_is_still_gated():
    """最重要的一條:延後載入不能繞過確認閘門。

    延後只改 schema 何時送;工具是在 ApprovalRequiredToolset 底下被呼叫的,所以事後
    才被 tool search 搜出來的 delete_ 工具照樣會被攔成 DeferredToolRequests。
    """
    EXECUTED.clear()
    ts = FunctionToolset()
    ts.add_function(create_dim)
    ts.add_function(delete_dim)
    # delete_dim 不在 eager 清單裡 → 標成延後,再包上確認閘門(build_agent 的順序)
    deferred = ts.prepared(_defer_non_eager(frozenset({"create_dim"})))
    gated = ApprovalRequiredToolset(deferred, lambda ctx, td, args: is_destructive(td.name))

    def model_func(messages: list, info: AgentInfo) -> ModelResponse:
        already = any(isinstance(p, ToolCallPart)
                      for m in messages if isinstance(m, ModelResponse) for p in m.parts)
        if not already:
            return ModelResponse(parts=[ToolCallPart(tool_name="delete_dim", args={"dim_id": 7})])
        return ModelResponse(parts=[TextPart(content="收工")])

    agent = Agent(FunctionModel(model_func), toolsets=[gated],
                  output_type=[str, DeferredToolRequests])
    first = asyncio.run(stream_turn(agent, prompt="刪掉舊的"))
    assert isinstance(first.output, DeferredToolRequests), "延後的破壞性工具必須仍被攔下"
    assert {c.tool_name for c in first.output.approvals} == {"delete_dim"}
    assert EXECUTED == [], "攔住時不可以已經執行"


if __name__ == "__main__":
    for fn in [test_is_destructive, test_stream_turn_emits_live_events,
               test_destructive_tool_is_gated_and_denied, test_destructive_tool_approved_executes,
               test_eager_tools_are_sent_up_front_and_the_rest_deferred,
               test_none_defers_everything, test_deferring_removes_no_tool,
               test_eager_tools_env_override, test_every_eager_tool_is_non_destructive,
               test_deferred_schemas_never_reach_the_wire,
               test_a_deferred_destructive_tool_is_still_gated]:
        fn()
        print(f"  ✓ {fn.__name__}")
    print("all passed")

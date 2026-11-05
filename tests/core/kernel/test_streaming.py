"""Incomplete/interleaved calls never bypass validation or permission hooks."""

import asyncio

import pytest
from langchain_core.messages import HumanMessage

from capabilities.hitl.approval_manager import ApprovalManager, ApprovalTimeoutError
from core.kernel.events import event_scope
from core.kernel.loop import KernelLoop
from core.kernel.state import AgentState
from core.kernel.streaming import collect_stream
from tests.test_kernel_loop import FakeLLMProvider, FakeTool, SpyMiddleware


class Chunks(FakeLLMProvider):
    def __init__(self, chunks):
        super().__init__("")
        self.chunks = chunks
        self.closed = False

    async def chat_stream(self, *args, **kwargs):
        try:
            for chunk in self.chunks:
                yield chunk
        finally:
            self.closed = True


async def test_interleaved_tools_and_unicode_arguments():
    provider = Chunks(
        [
            {
                "delta": "请稍候",
                "reasoning_delta": "not public",
                "tool_calls_delta": [
                    {
                        "index": 1,
                        "id": "b",
                        "function": {"name": "second", "arguments": '{"text":"中'},
                    },
                    {"index": 0, "id": "a", "function": {"name": "fir", "arguments": '{"x":'}},
                ],
            },
            {
                "tool_calls_delta": [
                    {"index": 0, "function": {"name": "st", "arguments": "1}"}},
                    {"index": 1, "function": {"arguments": '文"}'}},
                ]
            },
            {"finish_reason": "tool_calls"},
        ]
    )
    events = []

    async def sink(event):
        events.append(event)

    with event_scope(sink):
        response = await collect_stream(provider, [], None)
    assert response.content == "请稍候"
    assert response.reasoning_content == "not public"
    assert response.tool_calls == [
        {"id": "a", "name": "first", "args": {"x": 1}},
        {"id": "b", "name": "second", "args": {"text": "中文"}},
    ]
    assert len(events) == 2
    assert events[0].type == "reasoning" and events[0].data == {"delta": "not public"}
    assert events[1].type == "token" and events[1].data == {"delta": "请稍候"}
    assert provider.closed


@pytest.mark.parametrize(
    "part,finish",
    [
        ({"name": "fake_tool", "arguments": '{"x":'}, "tool_calls"),
        ({"name": "fake_tool", "arguments": "[]"}, "tool_calls"),
        ({"arguments": "{}"}, "tool_calls"),
        ({"name": "fake_tool", "arguments": "{}"}, "length"),
        ({"name": "fake_tool", "arguments": "{}"}, "stop"),
        ({"name": "fake_tool", "arguments": "{}"}, None),
    ],
)
async def test_bad_stream_never_runs_a_tool(part, finish):
    class SpyTool(FakeTool):
        async def run(self, **kwargs):
            pytest.fail("Incomplete call executed")

    provider = Chunks(
        [{"tool_calls_delta": [{"index": 0, "function": part}]}, {"finish_reason": finish}]
    )
    with pytest.raises(ValueError):
        await KernelLoop(provider, [SpyTool()]).run_once(AgentState(messages=[]), stream=True)
    assert provider.closed


@pytest.mark.parametrize("index", [-1, 128, "0"])
async def test_invalid_stream_index_closes_provider(index):
    provider = Chunks([{"tool_calls_delta": [{"index": index}]}])
    with pytest.raises(ValueError, match="index"):
        await collect_stream(provider, [], None)
    assert provider.closed


async def test_large_arguments_rejected():
    with pytest.raises(ValueError, match="limit"):
        await collect_stream(
            Chunks([{"tool_calls_delta": [{"function": {"arguments": "x" * 1_000_001}}]}]), [], None
        )


async def test_stream_path_fires_the_same_middleware_hooks():
    log = []

    class Hooks(SpyMiddleware):
        async def before_tool(self, name, args, state):
            log.append("before_tool")
            return True, None

        async def after_tool(self, name, args, result, state):
            log.append("after_tool")
            assert result.success
            return state

    mw = Hooks("mw")
    provider = Chunks(
        [
            {"tool_calls_delta": [{"function": {"name": "fake_tool", "arguments": "{}"}}]},
            {"finish_reason": "tool_calls"},
        ]
    )
    state = await KernelLoop(provider, [FakeTool()], [mw]).run_once(
        AgentState(messages=[HumanMessage(content="hi")]), stream=True
    )
    assert state["messages"][-1].content == "fake_result"
    assert mw.execution_log == ["mw_before", "mw_after"]
    assert log == ["before_tool", "after_tool"]


async def test_api_decision_replay_owner_and_cleanup():
    manager = ApprovalManager("api", timeout_seconds=1)
    ready = asyncio.Event()
    approval_id = None

    async def sink(event):
        nonlocal approval_id
        approval_id = event.data["approval_id"]
        ready.set()

    with event_scope(sink):
        task = asyncio.create_task(manager.request_approval("write", {"x": 1}, "session"))
    await ready.wait()
    with pytest.raises(KeyError):
        manager.decide(approval_id, "another-session", True)
    manager.decide(approval_id, "session", False)
    with pytest.raises(ValueError):
        manager.decide(approval_id, "session", True)
    assert await task is False
    assert not manager.pending and not manager._decisions


async def test_timeout_and_cancel_clear_all_futures():
    manager = ApprovalManager("api", timeout_seconds=0.01)
    with pytest.raises(ApprovalTimeoutError):
        await manager.request_approval("write", {}, "s")
    assert not manager.pending and not manager._decisions
    task = asyncio.create_task(manager.request_approval("write", {}, "s"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not manager.pending and not manager._decisions

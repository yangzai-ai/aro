"""Full-turn traces include all tool outcomes and the final assistant answer."""

import asyncio
import json

import pytest
from langchain_core.messages import HumanMessage

from core.kernel.loop import KernelLoop
from core.kernel.state import AgentState
from core.kernel.trace import AgentTrace, TraceMiddleware, print_trace
from core.llm.provider import LLMResponse
from core.tools.base import PermissionLevel, ToolResult
from tests.capabilities.hitl.test_integration import make_engine
from tests.test_kernel_loop import FakeLLMProvider, FakeTool


def test_trace_model_and_print(capsys):
    trace = AgentTrace(user_input="hello", agent_response="world")
    trace.finalize()
    assert trace.duration_ms >= 0 and trace.end_time >= trace.start_time
    json.dumps(trace.to_dict())
    print_trace(trace)
    assert trace.trace_id in capsys.readouterr().out


async def test_trace_multiple_rounds_and_repeated_tools(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, enabled=False)

    class MultiRound(FakeLLMProvider):
        def __init__(self):
            super().__init__("")
            self.round = 0

        async def chat(self, messages, tools=None, **kwargs):
            self.round += 1
            if self.round in {1, 2}:
                return LLMResponse(
                    content="",
                    tool_calls=[
                        {
                            "id": str(self.round),
                            "name": "query_tickets",
                            "args": {"status": "open" if self.round == 1 else "resolved"},
                        }
                    ],
                )
            return LLMResponse(content="final answer")

    engine.loop.llm = MultiRound()
    state = await engine.run("two queries")
    trace = engine.trace_middleware.get_latest_trace()
    assert trace.agent_response == "final answer"
    assert len(trace.tool_calls) == 2
    assert [call.result["count"] for call in trace.tool_calls] == [2, 1]
    assert trace.tool_calls[0].args["status"] == "open"
    assert trace.tool_calls[0].start_time <= trace.tool_calls[1].start_time
    first_id = trace.trace_id
    await engine.run("second turn", state)
    second = engine.trace_middleware.get_latest_trace()
    assert second.trace_id != first_id and second.user_input == "second turn"
    assert second.thread_id == trace.thread_id
    assert second.tool_calls == []


@pytest.mark.parametrize("failure", ["exception", "result", "missing", "write"])
async def test_failed_tools_are_recorded(failure, capsys):
    class Failing(FakeTool):
        @property
        def permission(self) -> PermissionLevel:
            return "write" if failure == "write" else "read"

        async def run(self, **kwargs):
            if failure == "exception":
                raise ValueError("broken tool")
            return ToolResult(False, error="failed result")

    tool = Failing()
    name = "missing" if failure == "missing" else tool.name
    trace = TraceMiddleware()
    loop = KernelLoop(FakeLLMProvider("", [{"id": "a", "name": name, "args": {}}]), [tool], [trace])
    await loop.run_once(AgentState(messages=[HumanMessage(content="test")]))
    latest = trace.get_latest_trace()
    assert len(latest.tool_calls) == 1 and not latest.tool_calls[0].success
    assert latest.tool_calls[0].error and latest.tool_calls[0].duration_ms >= 0
    print_trace(latest)
    assert "FAILED" in capsys.readouterr().out


async def test_trace_errors_and_limit_are_finalized(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, enabled=False)
    engine.loop.llm = FakeLLMProvider(
        "", [{"id": "a", "name": "query_tickets", "args": {}}]
    )
    with pytest.raises(RuntimeError, match="limit"):
        await engine.run("loop", max_tool_rounds=1)
    assert len(engine.trace_middleware.get_latest_trace().tool_calls) == 2
    assert engine.trace_middleware.get_latest_trace().error == "RuntimeError"
    assert engine.trace_middleware.current_trace is None
    with pytest.raises(ValueError):
        await engine.run("bad", max_tool_rounds=-1)

    class Broken(FakeLLMProvider):
        async def chat(self, *args, **kwargs):
            raise RuntimeError("LLM offline")

    engine.loop.llm = Broken("")
    with pytest.raises(RuntimeError, match="LLM offline"):
        await engine.run("failed turn")
    assert engine.trace_middleware.get_latest_trace().error == "RuntimeError"
    assert engine.trace_middleware.get_latest_trace().agent_response == ""
    print_trace(engine.trace_middleware.get_latest_trace())


async def test_one_engine_does_not_mix_concurrent_turns(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, enabled=False)
    entered, release = asyncio.Event(), asyncio.Event()

    class Waiting(FakeLLMProvider):
        async def chat(self, *args, **kwargs):
            entered.set()
            await release.wait()
            return LLMResponse(content="done")

    engine.loop.llm = Waiting("")
    first = asyncio.create_task(engine.run("one"))
    await entered.wait()
    try:
        with pytest.raises(RuntimeError, match="active"):
            await engine.run("two")
    finally:
        release.set()
        await first
    assert len(engine.trace_middleware.get_all_traces()) == 1


async def test_empty_observer_and_snapshots():
    trace = TraceMiddleware()
    assert trace.get_latest_trace() is None
    state = AgentState(messages=[])
    assert await trace.after_tool("missing", {}, ToolResult(True), state) is state
    assert await trace.after(state) is state
    await trace.before(state)
    args, output = {"x": [1]}, {"y": [2]}
    await trace.before_tool("t", args, state)
    await trace.after_tool("t", args, ToolResult(True, output), state)
    args["x"].append(3)
    output["y"].append(4)
    await trace.after(state)
    assert trace.get_latest_trace().tool_calls[0].args == {"x": [1]}
    assert trace.get_latest_trace().tool_calls[0].result == {"y": [2]}
    assert trace.get_all_traces()[0].agent_response == ""

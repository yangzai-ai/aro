"""Graph migration regressions without changing any capability contract/test."""

import asyncio
from contextvars import ContextVar

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from core.config_loader import AgentConfig, LLMConfig
from core.kernel.engine import AgentEngine
from core.kernel.events import event_scope
from core.kernel.loop import KernelLoop, KernelRun
from core.kernel.state import AgentState
from core.llm.provider import LLMResponse
from core.tools.base import ToolResult
from tests.test_kernel_loop import FakeLLMProvider, FakeTool, SpyHook


@pytest.fixture
async def engine(tmp_path):
    config = tmp_path / "agent.yml"
    config.write_text("capabilities: []\n", encoding="utf-8")
    value = AgentEngine(
        LLMConfig(OPENAI_API_KEY="test-only", OPENAI_API_BASE="http://unused.test"),
        AgentConfig(config),
    )
    try:
        yield value
    finally:
        await value.close()


@pytest.mark.parametrize("budget", [0, 1, 8, 12])
async def test_last_allowed_tool_batch_executes_before_original_limit_error(budget):
    class CountingTool(FakeTool):
        calls = 0

        async def run(self, **kwargs):
            self.calls += 1
            return ToolResult(True, self.calls)

    tool = CountingTool()
    hooks = SpyHook("count")
    loop = KernelLoop(
        FakeLLMProvider("", [{"id": "repeated", "name": tool.name, "args": {}}]),
        [tool],
        [hooks],
    )
    frame = KernelRun(AgentState(messages=[]))
    with pytest.raises(RuntimeError) as caught:
        await loop._run(frame, max_tool_rounds=budget)
    assert str(caught.value) == "Tool-call limit reached; simplify the question"
    assert tool.calls == budget + 1
    assert sum(isinstance(m, ToolMessage) for m in frame.state["messages"]) == budget + 1
    assert hooks.execution_log == ["count_before", "count_after"] * (budget + 1)


async def test_final_answer_in_last_slot_and_complete_history_are_preserved(engine):
    original_human = HumanMessage(content="same content")
    original_ai = AIMessage(content="same content")
    temporary = SystemMessage(content="remove this")

    class Replace(SpyHook):
        async def before(self, state):
            return AgentState(
                messages=[m for m in state["messages"] if m is not temporary],
                context=dict(state["context"]),
            )

        async def after_tool(self, name, args, result, state):
            return AgentState(messages=list(state["messages"]), context=dict(state["context"]))

    class TwoRounds(FakeLLMProvider):
        seen = []

        async def chat(self, messages, tools=None, **kwargs):
            self.seen.append(list(messages))
            if isinstance(messages[-1], ToolMessage):
                return LLMResponse(content="final")
            return LLMResponse(
                content="", tool_calls=[{"id": "call", "name": "fake_tool", "args": {}}]
            )

    provider = TwoRounds("")
    engine.loop = KernelLoop(provider, [FakeTool()], [engine.trace_hook, Replace("replace")])
    state = AgentState(
        messages=[temporary, original_human, original_ai], context={"thread_id": "known"}
    )
    result = await engine.run("new request", state, max_tool_rounds=1)
    assert result["messages"][-1].content == "final"
    assert len(result["messages"]) == 6 and len(provider.seen) == 2
    assert result["messages"][0] is original_human and result["messages"][1] is original_ai
    assert original_human.id is None and original_ai.id is None
    assert not any(m is temporary for m in result["messages"])
    assert all(provider.seen[0][i] is provider.seen[1][i] for i in range(3))
    assert set(result) == {"messages", "context"}
    assert len(engine.trace_hook.traces) == 1
    assert engine.trace_hook.get_latest_trace().agent_response == "final"


async def test_trace_finishes_latest_replaced_state_when_next_hook_raises(engine):
    class Replace(SpyHook):
        async def before(self, state):
            return AgentState(
                messages=[*state["messages"], AIMessage(content="observed before failure")],
                context=dict(state["context"]),
            )

    class Fail(SpyHook):
        async def before(self, state):
            raise ValueError("before failed")

    engine.loop = KernelLoop(FakeLLMProvider("unreachable"), [], [Replace("replace"), Fail("fail")])
    with pytest.raises(ValueError, match="before failed"):
        await engine.run("request")
    trace = engine.trace_hook.get_latest_trace()
    assert trace.error == "ValueError" and trace.agent_response == "observed before failure"
    assert (
        len(engine.trace_hook.traces) == 1 and engine.trace_hook.current_trace is None
    )


async def test_node_context_writes_cross_nodes_but_not_concurrent_runs_or_caller():
    scope = ContextVar("graph_test_identity", default="caller")
    observations = []

    class Identity(SpyHook):
        async def before(self, state):
            scope.set(state["context"]["user_id"])
            return state

        async def after(self, state):
            observations.append(("after", state["context"]["user_id"], scope.get()))
            return state

    class Read(FakeTool):
        async def run(self, **kwargs):
            await asyncio.sleep(0)
            return ToolResult(True, scope.get())

    async def run(user):
        loop = KernelLoop(
            FakeLLMProvider("", [{"id": user, "name": "fake_tool", "args": {}}]),
            [Read()],
            [Identity(user)],
        )
        events = []

        async def receive(event):
            observations.append(("event", user, scope.get()))
            events.append(event)

        with event_scope(receive):
            result = await loop.run_once(AgentState(messages=[], context={"user_id": user}))
        assert result["messages"][-1].content == user
        assert [e.type for e in events] == ["tool_call", "tool_result"]

    await asyncio.gather(run("alice"), run("bob"))
    assert scope.get() == "caller"
    assert all(expected == actual for _, expected, actual in observations)
    assert len(observations) == 6


async def test_cancelled_graph_drains_pending_before_tool_without_executing():
    pending = asyncio.get_running_loop().create_future()
    entered, released = asyncio.Event(), asyncio.Event()

    class Guard(SpyHook):
        async def before_tool(self, name, args, state):
            entered.set()
            try:
                return await pending
            finally:
                released.set()

    class MustNotRun(FakeTool):
        async def run(self, **kwargs):
            pytest.fail("cancelled authorization must not execute the tool")

    loop = KernelLoop(
        FakeLLMProvider("", [{"id": "call", "name": "fake_tool", "args": {}}]),
        [MustNotRun()],
        [Guard("guard")],
    )
    task = asyncio.create_task(loop.run_once(AgentState(messages=[])))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pending.cancelled() and released.is_set() and task.done()

"""Complete Hook lifecycle, trusted identity restoration and cancellation cleanup."""

import asyncio
from copy import deepcopy

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from core.kernel.engine import AgentEngine
from core.kernel.state import AgentState
from tests.core.kernel.test_discovery import Plugin, config, llm
from tests.test_kernel_loop import FakeLLMProvider, SpyHook


class LifecycleHook(SpyHook):
    def __init__(self, name="probe", *, restored=None, fail_start=False, fail_stop=False):
        super().__init__(name)
        self.events = []
        self.restored = restored
        self.fail_start, self.fail_stop = fail_start, fail_stop

    async def on_start(self, cfg):
        self.events.append(("start", deepcopy(cfg)))
        if self.fail_start:
            raise ValueError("startup secret")

    async def on_session_load(self, session_id):
        self.events.append(("load", session_id))
        return self.restored

    async def on_session_close(self, session_id):
        self.events.append(("close", session_id))

    async def on_stop(self):
        self.events.append(("stop",))
        if self.fail_stop:
            raise asyncio.CancelledError()


class HookPlugin(Plugin):
    def __init__(self, *hooks):
        super().__init__()
        self._hooks = list(hooks)

    def hooks(self):
        return self._hooks


async def make_engine(tmp_path, *hooks, sync=False):
    args = (llm(), config(tmp_path, capabilities=[]))
    if sync:
        engine = AgentEngine(*args, capabilities=[HookPlugin(*hooks)])
    else:
        engine = await AgentEngine.create(*args, capabilities=[HookPlugin(*hooks)])
    engine.loop.llm = FakeLLMProvider("answer")
    return engine


def stages(hook):
    return [event[0] for event in hook.events]


async def test_start_runs_once_after_create_before_first_round(tmp_path):
    hook = LifecycleHook()
    engine = await make_engine(tmp_path, hook)
    try:
        assert stages(hook) == ["start"]
        await asyncio.gather(engine.start(), engine.start())
        state = await engine.run("one")
        await engine.run("two", state)
        assert stages(hook) == ["start", "load", "load"]
        assert "OPENAI_API_KEY" not in repr(hook.events[0])
    finally:
        await engine.close()
    assert stages(hook)[-2:] == ["close", "stop"]


async def test_sync_constructor_defers_start_to_first_run(tmp_path):
    hook = LifecycleHook()
    engine = await make_engine(tmp_path, hook, sync=True)
    try:
        assert hook.events == []
        await engine.run("hello")
        assert stages(hook) == ["start", "load"]
    finally:
        await engine.close()


async def test_restoration_precedes_new_input_and_preserves_trusted_context(tmp_path):
    snapshot = AgentState(
        messages=[HumanMessage(content="old question"), AIMessage(content="old answer")],
        context={"saved": 7},
    )
    hook = LifecycleHook(restored=snapshot)
    engine = await make_engine(tmp_path, hook)
    caller = AgentState(
        messages=[],
        tenant_id="top",
        context={"thread_id": "session", "tenant_id": "tenant", "user_id": "alice", "host": True},
    )
    try:
        state = await engine.run("new question", caller)
        assert [message.content for message in state["messages"]] == [
            "old question",
            "old answer",
            "new question",
            "answer",
        ]
        assert state["context"]["thread_id"] == "session"
        assert state["context"]["tenant_id"] == "tenant" and state["context"]["user_id"] == "alice"
        assert state["context"]["saved"] == 7 and state["context"]["host"] is True
        assert state["tenant_id"] == "top"
        assert len(snapshot["messages"]) == 2 and caller["messages"] == []
    finally:
        await engine.close()


async def test_first_non_none_load_wins_even_for_empty_messages(tmp_path):
    first = LifecycleHook("none")
    winner = LifecycleHook("winner", restored=AgentState(messages=[]))
    skipped = LifecycleHook("skipped", restored=AgentState(messages=[AIMessage(content="wrong")]))
    engine = await make_engine(tmp_path, first, winner, skipped)
    try:
        state = await engine.run("hello")
        assert [m.content for m in state["messages"]] == ["hello", "answer"]
        assert stages(first) == stages(winner) == ["start", "load"]
        assert stages(skipped) == ["start"]
    finally:
        await engine.close()


async def test_session_close_and_stop_are_idempotent(tmp_path):
    hook = LifecycleHook()
    engine = await make_engine(tmp_path, hook)
    state = await engine.run("hello", AgentState(messages=[], context={"thread_id": "s"}))
    await asyncio.gather(engine.close_session("s"), engine.close_session("s"))
    with pytest.raises(RuntimeError, match="Session is closed"):
        await engine.run("again", state)
    await asyncio.gather(engine.close(), engine.close())
    assert stages(hook) == ["start", "load", "close", "stop"]
    assert engine.llm._client.is_closed


@pytest.mark.parametrize("key", ["thread_id", "tenant_id", "user_id", "top"])
async def test_restored_identity_conflict_is_rejected_before_model(tmp_path, key):
    restored = AgentState(messages=[], context={})
    if key == "top":
        restored["tenant_id"] = "other"
    else:
        restored["context"][key] = "other"
    hook = LifecycleHook(restored=restored)
    engine = await make_engine(tmp_path, hook)
    caller = AgentState(
        messages=[],
        tenant_id="top",
        context={"thread_id": "s", "tenant_id": "tenant", "user_id": "alice"},
    )
    try:
        with pytest.raises(ValueError, match="identity conflict"):
            await engine.run("new", caller)
        assert caller["messages"] == [] and engine.trace_hook.get_all_traces() == []
    finally:
        await engine.close()


@pytest.mark.parametrize("key", ["tenant_id", "user_id"])
async def test_restored_identity_cannot_be_injected_when_host_did_not_supply_it(tmp_path, key):
    hook = LifecycleHook(restored=AgentState(messages=[], context={key: "injected"}))
    engine = await make_engine(tmp_path, hook)
    try:
        with pytest.raises(ValueError, match="identity conflict"):
            await engine.run("new", AgentState(messages=[], context={"thread_id": "s"}))
    finally:
        await engine.close()


@pytest.mark.parametrize(
    "restored", [{}, {"messages": "bad"}, {"messages": ["bad"]}, {"messages": [], "context": []}]
)
async def test_invalid_restored_state_rejected(tmp_path, restored):
    engine = await make_engine(tmp_path, LifecycleHook(restored=restored))
    try:
        with pytest.raises(ValueError, match="Invalid restored"):
            await engine.run("new")
    finally:
        await engine.close()


async def test_start_failure_rolls_back_entered_hooks_and_capability(tmp_path):
    first, failing, skipped = (
        LifecycleHook("first"),
        LifecycleHook("failing", fail_start=True),
        LifecycleHook("skipped"),
    )
    plugin = HookPlugin(first, failing, skipped)
    with pytest.raises(ValueError, match="startup secret"):
        await AgentEngine.create(llm(), config(tmp_path, capabilities=[]), capabilities=[plugin])
    assert stages(first) == stages(failing) == ["start", "stop"]
    assert skipped.events == [] and plugin.closed


async def test_stop_cancellation_does_not_skip_other_resources(tmp_path):
    first, failing = LifecycleHook("first"), LifecycleHook("last", fail_stop=True)
    engine = await make_engine(tmp_path, first, failing)
    await engine.run("hello")
    with pytest.raises(RuntimeError, match="remaining cleanup"):
        await engine.close()
    assert stages(first)[-2:] == stages(failing)[-2:] == ["close", "stop"]
    assert engine.capabilities[0].closed and engine.llm._client.is_closed
    assert list(engine.checkpointer.list(None)) == []


async def test_cancelled_start_rolls_back_partial_hook(tmp_path):
    entered = asyncio.Event()

    class Blocking(LifecycleHook):
        async def on_start(self, cfg):
            self.events.append(("start",))
            entered.set()
            await asyncio.Event().wait()

    hook = Blocking()
    plugin = HookPlugin(hook)
    task = asyncio.create_task(
        AgentEngine.create(llm(), config(tmp_path, capabilities=[]), capabilities=[plugin])
    )
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stages(hook) == ["start", "stop"] and plugin.closed


async def test_concurrent_close_waiter_cancelled_cleanup_still_runs_once(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()

    class Blocking(LifecycleHook):
        async def on_stop(self):
            self.events.append(("stop",))
            entered.set()
            await release.wait()

    hook = Blocking()
    engine = await make_engine(tmp_path, hook)
    first = asyncio.create_task(engine.close())
    await asyncio.wait_for(entered.wait(), 2)
    second = asyncio.create_task(engine.close())
    first.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    assert stages(hook) == ["start", "stop"]
    assert engine.capabilities[0].closed and engine.llm._client.is_closed


async def test_session_hook_failure_still_stops_and_releases_everything(tmp_path):
    class Failing(LifecycleHook):
        async def on_session_close(self, session_id):
            await super().on_session_close(session_id)
            raise ValueError("do not log credentials")

    first, failing = LifecycleHook(), Failing()
    engine = await make_engine(tmp_path, first, failing)
    await engine.run("hello")
    with pytest.raises(RuntimeError, match="remaining cleanup"):
        await engine.close()
    assert stages(first)[-2:] == stages(failing)[-2:] == ["close", "stop"]
    assert engine.llm._client.is_closed and engine.capabilities[0].closed

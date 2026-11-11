"""Trusted task identity survives graph boundaries but never escapes an Engine run."""

import asyncio

import pytest

from core.kernel.context import bind_thread_id, get_thread_id
from core.kernel.state import AgentState
from core.llm.provider import LLMResponse
from tests.core.kernel.test_hook_lifecycle import make_engine
from tests.test_kernel_loop import FakeLLMProvider, SpyHook


def test_nested_binding_restores_parent_and_exception_resets():
    assert get_thread_id() is None
    with bind_thread_id("outer"):
        assert get_thread_id() == "outer"
        with pytest.raises(RuntimeError), bind_thread_id("inner"):
            assert get_thread_id() == "inner"
            raise RuntimeError("fail")
        assert get_thread_id() == "outer"
    assert get_thread_id() is None


@pytest.mark.parametrize("value", ["", "   ", None, 12])
def test_invalid_binding_rejected(value):
    with pytest.raises(ValueError), bind_thread_id(value):
        pytest.fail("invalid binding accepted")
    assert get_thread_id() is None


async def test_concurrent_engine_graph_hooks_keep_separate_identities(tmp_path):
    seen, entered, release = [], asyncio.Event(), asyncio.Event()

    class Observer(SpyHook):
        async def before(self, state):
            seen.append((state["context"]["thread_id"], get_thread_id()))
            if len(seen) == 2:
                entered.set()
            await release.wait()
            return state

        async def after(self, state):
            assert get_thread_id() == state["context"]["thread_id"]
            return state

    first = await make_engine(tmp_path, Observer("a"))
    second = await make_engine(tmp_path, Observer("b"))
    tasks = [
        asyncio.create_task(
            engine.run("hello", AgentState(messages=[], context={"thread_id": identity}))
        )
        for engine, identity in [(first, "one"), (second, "two")]
    ]
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert sorted(seen) == [("one", "one"), ("two", "two")]
        assert get_thread_id() is None
        release.set()
        await asyncio.gather(*tasks)
        assert get_thread_id() is None
    finally:
        release.set()
        await first.close()
        await second.close()


async def test_nested_engine_restores_parent_identity(tmp_path):
    child = await make_engine(tmp_path)
    parent = await make_engine(tmp_path)

    class Nested(FakeLLMProvider):
        async def chat(self, messages, tools=None, **kwargs):
            assert get_thread_id() == "parent"
            await child.run("child input", AgentState(messages=[], context={"thread_id": "child"}))
            assert get_thread_id() == "parent"
            return LLMResponse(content="parent done")

    parent.loop.llm = Nested("")
    try:
        await parent.run("parent input", AgentState(messages=[], context={"thread_id": "parent"}))
        assert get_thread_id() is None
    finally:
        await parent.close()
        await child.close()


@pytest.mark.parametrize("cancel", [True, False])
async def test_failure_and_cancellation_restore_callers_scope(tmp_path, cancel):
    entered = asyncio.Event()
    engine = await make_engine(tmp_path)

    class Failure(FakeLLMProvider):
        async def chat(self, messages, tools=None, **kwargs):
            assert get_thread_id() == "engine"
            entered.set()
            if cancel:
                await asyncio.Event().wait()
            raise RuntimeError("model failed")

    engine.loop.llm = Failure("")

    async def caller():
        with bind_thread_id("caller"):
            try:
                await engine.run("input", AgentState(messages=[], context={"thread_id": "engine"}))
            finally:
                assert get_thread_id() == "caller"

    task = asyncio.create_task(caller())
    await entered.wait()
    if cancel:
        task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
            await task
        assert get_thread_id() is None
    finally:
        await engine.close()

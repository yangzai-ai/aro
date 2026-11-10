"""Snapshots are infrastructure; explicit input never resumes saved work."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import ToolMessage, messages_to_dict

from core.config_loader import AgentConfig, LLMConfig
from core.kernel import loop as kernel_loop
from core.kernel.checkpoint import default_checkpointer
from core.kernel.engine import AgentEngine
from core.kernel.loop import KernelLoop
from core.kernel.state import AgentState
from core.llm.provider import LLMResponse
from tests.test_kernel_loop import FakeLLMProvider, FakeTool


def config(tmp_path):
    path = tmp_path / "agent.yml"
    path.write_text("capabilities: []\n", encoding="utf-8")
    return LLMConfig(OPENAI_API_KEY="test", OPENAI_API_BASE="http://unused.test"), AgentConfig(path)


async def test_engine_owned_saver_roundtrip_threads_and_close(tmp_path):
    first = AgentEngine(*config(tmp_path))
    second = AgentEngine(*config(tmp_path))
    first.loop.llm = FakeLLMProvider("answer")
    assert type(first.checkpointer) is type(default_checkpointer())
    assert first.checkpointer is not second.checkpointer
    try:
        state = await first.run("hello")
        identity = state["context"]["thread_id"]
        checkpoint = await first.checkpointer.aget_tuple({"configurable": {"thread_id": identity}})
        values = checkpoint.checkpoint["channel_values"]
        assert messages_to_dict(values["business"]["messages"]) == messages_to_dict(
            state["messages"]
        )
        assert values["business"]["context"] == state["context"]
        assert values["calls"] == [] and values["rounds"] == 1
        assert (
            await second.checkpointer.aget_tuple({"configurable": {"thread_id": identity}}) is None
        )
        await first.run("different conversation")
        assert len(first._checkpoint_threads) == 2
        await asyncio.gather(first.close(), first.close())
        assert list(first.checkpointer.list(None)) == []
    finally:
        await first.close()
        await second.close()


async def test_run_once_derives_thread_without_context_and_uses_existing_one(monkeypatch):
    savers = []
    original = kernel_loop.StateGraph.compile

    def compile_graph(self, **kwargs):
        savers.append(kwargs["checkpointer"])
        return original(self, **kwargs)

    monkeypatch.setattr(kernel_loop.StateGraph, "compile", compile_graph)
    loop = KernelLoop(FakeLLMProvider("answer"))
    for initial in [
        AgentState(messages=[]),
        AgentState(messages=[], context={"thread_id": "provided"}),
    ]:
        state = await loop.run_once(initial)
        identity = state["context"]["thread_id"]
        assert identity and state is initial
        record = await savers[-1].aget_tuple({"configurable": {"thread_id": identity}})
        assert record.checkpoint["channel_values"]["business"]["context"]["thread_id"] == identity
    assert state["context"]["thread_id"] == "provided"
    assert savers[0] is not savers[1]  # standalone runs own no retained session


async def test_checkpointed_conversations_match_unsaved_graph_byte_for_byte(tmp_path, monkeypatch):
    class Provider(FakeLLMProvider):
        async def chat(self, messages, tools=None, **kwargs):
            if isinstance(messages[-1], ToolMessage):
                return LLMResponse(content="answer:" + messages[-1].content)
            return LLMResponse(
                content="", tool_calls=[{"id": "same-call", "name": "fake_tool", "args": {}}]
            )

    async def conversation():
        engine = AgentEngine(*config(tmp_path))
        engine.loop = KernelLoop(Provider(""), [FakeTool()], [engine.trace_hook])
        try:
            state = AgentState(messages=[], context={"thread_id": "same-thread", "custom": "kept"})
            outputs = []
            for text in ["first", "second", "third"]:
                state = await engine.run(text, state, max_tool_rounds=1)
                outputs.append(
                    json.dumps(
                        messages_to_dict(state["messages"]), ensure_ascii=False, sort_keys=True
                    ).encode()
                )
            return outputs
        finally:
            await engine.close()

    saved = await conversation()
    original = kernel_loop.StateGraph.compile

    def no_checkpoint(self, **kwargs):
        kwargs.pop("checkpointer")
        return original(self, **kwargs)

    monkeypatch.setattr(kernel_loop.StateGraph, "compile", no_checkpoint)
    assert await conversation() == saved


async def test_explicit_input_replaces_pending_calls_history_and_rounds(tmp_path):
    saver = default_checkpointer()
    engine = AgentEngine(*config(tmp_path), checkpointer=saver)

    class SideEffect(FakeTool):
        calls = 0

        async def run(self, **kwargs):
            self.calls += 1
            return await super().run(**kwargs)

    tool = SideEffect()

    async def fail_after_write(event):
        # after_tool errors are isolated in v1.4. A transport failure still
        # interrupts this same tools node after the external side effect.
        if event.type == "tool_result":
            raise RuntimeError("failed after side effect")

    engine.loop = KernelLoop(
        FakeLLMProvider("", [{"id": "old", "name": "fake_tool", "args": {}}]),
        [tool],
        [engine.trace_hook],
    )
    old = AgentState(messages=[], context={"thread_id": "same", "old_only": True})
    try:
        with pytest.raises(RuntimeError, match="failed after side effect"):
            await engine.run("old request", old, max_tool_rounds=0, on_event=fail_after_write)
        assert tool.calls == 1
        pending = await saver.aget_tuple({"configurable": {"thread_id": "same"}})
        assert pending.checkpoint["channel_values"]["calls"]  # unfinished tools→after boundary
        engine.loop.llm = FakeLLMProvider("fresh answer")
        fresh = AgentState(messages=[], context={"thread_id": "same", "new_only": True})
        result = await engine.run("fresh request", fresh, max_tool_rounds=0)
        assert result is fresh and tool.calls == 1
        assert [m.content for m in result["messages"]] == ["fresh request", "fresh answer"]
        record = await saver.aget_tuple({"configurable": {"thread_id": "same"}})
        values = record.checkpoint["channel_values"]
        assert values["rounds"] == 1 and values["calls"] == []
        assert "old_only" not in values["business"]["context"]
    finally:
        await engine.close()
    # Injected storage belongs to the caller; the engine must not delete it.
    assert await saver.aget_tuple({"configurable": {"thread_id": "same"}}) is not None


async def test_async_factory_uses_injected_native_saver_without_owning_it(tmp_path):
    saver = default_checkpointer()
    saver.adelete_thread = AsyncMock(wraps=saver.adelete_thread)
    engine = await AgentEngine.create(*config(tmp_path), checkpointer=saver)
    engine.loop.llm = FakeLLMProvider("saved")
    state = await engine.run("hello")
    await engine.close()
    assert engine.checkpointer is saver
    saver.adelete_thread.assert_not_awaited()
    assert await saver.aget_tuple({"configurable": {"thread_id": state["context"]["thread_id"]}})

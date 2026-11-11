"""Engine reconstruction and configuration changes through the actual plugin loader."""

import json
import subprocess
import sys

import pytest
import yaml
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from capabilities.session_persistence.capability import SessionPersistenceCapability
from core.config_loader import AgentConfig, LLMConfig
from core.kernel.context import get_thread_id
from core.kernel.engine import AgentEngine
from core.kernel.state import AgentState
from core.llm.provider import LLMResponse
from tests.test_kernel_loop import FakeLLMProvider, FakeTool


def settings():
    return LLMConfig(OPENAI_API_KEY="unused", OPENAI_API_BASE="http://unused.test")


def config(tmp_path, backend="memory", *, delete=False):
    path = tmp_path / "agent.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "capabilities": {
                    "session_persistence": {
                        "backend": backend,
                        "sqlite_path": str(tmp_path / "sessions.db"),
                        "delete_on_session_close": delete,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return AgentConfig(path)


def state(identity="trusted-thread"):
    return AgentState(
        messages=[],
        context={"thread_id": identity, "tenant_id": "tenant", "user_id": "alice"},
        tenant_id="tenant",
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_engine_reconstruction_restores_messages_and_keeps_identity(
    tmp_path, pg_double, backend
):
    cfg = config(tmp_path, backend)
    first = await AgentEngine.create(settings(), cfg)
    first.loop.llm = FakeLLMProvider("first answer")
    result = await first.run("first question", state())
    assert get_thread_id() is None
    await first.close()
    second = await AgentEngine.create(settings(), cfg)
    second.loop.llm = FakeLLMProvider("second answer")
    try:
        restored = await second.run("second question", state())
        assert [message.content for message in restored["messages"]] == [
            "first question",
            "first answer",
            "second question",
            "second answer",
        ]
        assert restored["tenant_id"] == "tenant"
        assert restored["context"]["user_id"] == "alice"
        assert restored["context"]["thread_id"] == result["context"]["thread_id"]
    finally:
        await second.close()


async def test_switch_backend_config_selects_real_implementations_without_kernel_changes(
    tmp_path, pg_double
):
    types = []
    for backend in ("memory", "sqlite", "postgres"):
        engine = await AgentEngine.create(settings(), config(tmp_path, backend))
        engine.loop.llm = FakeLLMProvider("answer")
        try:
            capability = engine.capabilities[0]
            assert isinstance(capability, SessionPersistenceCapability)
            types.append(type(capability.backend).__name__)
            await engine.run("hello", state())
            snapshot = await capability.backend.load("trusted-thread")
            assert snapshot["messages"][-1]["data"]["content"] == "answer"
        finally:
            await engine.close()
    assert types == ["MemoryBackend", "SqliteBackend", "PostgresBackend"]


@pytest.mark.parametrize("delete", [True, False])
async def test_explicit_delete_policy_controls_normal_shutdown_recovery(tmp_path, delete):
    cfg = config(tmp_path, "sqlite", delete=delete)
    first = await AgentEngine.create(settings(), cfg)
    first.loop.llm = FakeLLMProvider("old answer")
    await first.run("old question", state())
    await first.close()
    second = await AgentEngine.create(settings(), cfg)
    second.loop.llm = FakeLLMProvider("new answer")
    try:
        restored = await second.run("new question", state())
        assert len(restored["messages"]) == (2 if delete else 4)
    finally:
        await second.close()


@pytest.mark.parametrize("key", ["thread_id", "tenant_id", "user_id", "top"])
async def test_framework_rejects_persisted_identity_conflicts(tmp_path, key):
    engine = await AgentEngine.create(settings(), config(tmp_path))
    capability = engine.capabilities[0]
    snapshot = {
        "version": 1,
        "messages": [],
        "context": dict(state()["context"]),
        "tenant_id": "tenant",
    }
    if key == "top":
        snapshot["tenant_id"] = "intruder"
    else:
        snapshot["context"][key] = "intruder"
    await capability.backend.save("trusted-thread", snapshot)
    try:
        with pytest.raises(ValueError, match="identity conflict"):
            await engine.run("not sent", state())
        assert not engine.trace_hook.traces and get_thread_id() is None
    finally:
        await engine.close()


async def test_repaired_dangling_write_never_replays_tool(tmp_path):
    from langchain_core.messages import messages_to_dict

    class Counting(FakeTool):
        count = 0

        async def run(self, **kwargs):
            self.count += 1
            return await super().run(**kwargs)

    engine = await AgentEngine.create(settings(), config(tmp_path))
    capability = engine.capabilities[0]
    tool = Counting()
    engine.loop._tool_map[tool.name] = tool
    history = [
        HumanMessage(content="previous"),
        AIMessage(
            content="",
            tool_calls=[{"id": "pending", "name": tool.name, "args": {}}],
            additional_kwargs={"reasoning_content": "protocol"},
        ),
    ]
    await capability.backend.save(
        "trusted-thread",
        {
            "version": 1,
            "messages": messages_to_dict(history),
            "context": state()["context"],
            "tenant_id": "tenant",
        },
    )

    class Inspecting(FakeLLMProvider):
        async def chat(self, messages, tools=None, **kwargs):
            assert isinstance(messages[2], ToolMessage) and messages[2].status == "error"
            assert "执行状态未知" in messages[2].content
            assert messages[1].additional_kwargs["reasoning_content"] == "protocol"
            assert messages[3].content == "followup"
            return LLMResponse(content="please verify")

    engine.loop.llm = Inspecting("")
    try:
        await engine.run("followup", state())
        assert tool.count == 0
    finally:
        await engine.close()


def test_sqlite_restart_across_actual_processes(tmp_path):
    config(tmp_path, "sqlite")
    script = r"""
import asyncio, sys, json
from core.config_loader import AgentConfig, LLMConfig
from core.kernel.engine import AgentEngine
from core.kernel.state import AgentState
from tests.test_kernel_loop import FakeLLMProvider
async def main():
    engine = await AgentEngine.create(LLMConfig(OPENAI_API_KEY='test',OPENAI_API_BASE='http://unused.test'), AgentConfig(sys.argv[1]))
    engine.loop.llm = FakeLLMProvider('answer-'+sys.argv[2])
    try:
        state = await engine.run('question-'+sys.argv[2], AgentState(messages=[],context={'thread_id':'trusted-cross-process'}))
        print(json.dumps([m.content for m in state['messages']]))
    finally:
        await engine.close()
asyncio.run(main())
"""
    config_path = tmp_path / "agent.yml"
    first = subprocess.run(
        [sys.executable, "-c", script, str(config_path), "one"],
        capture_output=True,
        check=True,
        text=True,
    )
    second = subprocess.run(
        [sys.executable, "-c", script, str(config_path), "two"],
        capture_output=True,
        check=True,
        text=True,
    )
    assert json.loads(first.stdout) == ["question-one", "answer-one"]
    assert json.loads(second.stdout) == ["question-one", "answer-one", "question-two", "answer-two"]

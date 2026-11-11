"""Lossless messages, honest missing-result repair and plugin lifecycle."""

import json
import subprocess
import sys

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    messages_to_dict,
)

from capabilities.session_persistence.backends.memory import MemoryBackend
from capabilities.session_persistence.capability import SessionPersistenceCapability, get_capability
from capabilities.session_persistence.config import SessionPersistenceConfig
from capabilities.session_persistence.hook import UNKNOWN_TOOL_RESULT, SessionPersistenceHook
from core.kernel.context import bind_thread_id, get_thread_id
from core.kernel.state import AgentState


def assistant(*ids):
    return AIMessage(
        content="",
        tool_calls=[
            {"id": call_id, "name": "update_ticket", "args": {"x": [1]}} for call_id in ids
        ],
        additional_kwargs={"reasoning_content": "private protocol reasoning"},
    )


def test_langchain_roundtrip_retains_all_message_fields_and_json_shape():
    messages = [
        SystemMessage(content="system"),
        HumanMessage(content="hello"),
        assistant("a"),
        ToolMessage(content='{"ok":true}', tool_call_id="a", name="update_ticket"),
        AIMessage(content="done"),
    ]
    hook = SessionPersistenceHook(MemoryBackend())
    serialized = json.loads(json.dumps(hook._serialize_messages(messages), ensure_ascii=False))
    restored = hook._deserialize_messages(serialized)
    assert messages_to_dict(restored) == messages_to_dict(messages)
    assert restored[2].additional_kwargs["reasoning_content"] == "private protocol reasoning"
    assert restored[2].tool_calls[0]["args"] == {"x": [1]}
    assert restored[2].content == ""


def test_dangling_calls_are_completed_at_each_batch_not_history_end():
    original = [
        HumanMessage(content="one"),
        assistant("a", "b"),
        ToolMessage(content="recorded", tool_call_id="b"),
        HumanMessage(content="two"),
        assistant("a"),
        AIMessage(content="interrupted prose"),
    ]
    result = SessionPersistenceHook._fill_dangling_calls(original)
    assert [message.type for message in result] == [
        "human",
        "ai",
        "tool",
        "tool",
        "human",
        "ai",
        "tool",
        "ai",
    ]
    assert result[2].content == "recorded"
    assert result[3].tool_call_id == result[6].tool_call_id == "a"
    assert result[3].content == result[6].content == UNKNOWN_TOOL_RESULT
    assert result[3].status == "error" and "执行状态未知" in result[3].content
    assert result[4].content == "two" and result[-1].content == "interrupted prose"
    assert len(original) == 6
    assert result[1].additional_kwargs == original[1].additional_kwargs


def test_complete_batch_and_non_tool_history_are_unchanged():
    original = [
        HumanMessage(content="one"),
        assistant("a", "b"),
        ToolMessage(content="b", tool_call_id="b"),
        ToolMessage(content="a", tool_call_id="a"),
        AIMessage(content="done"),
    ]
    assert SessionPersistenceHook._fill_dangling_calls(original) == original
    assert SessionPersistenceHook._fill_dangling_calls([]) == []


@pytest.mark.parametrize(
    "messages",
    [
        [assistant("same", "same")],
        [assistant("")],
        [ToolMessage(content="orphan", tool_call_id="a")],
        [assistant("a"), ToolMessage(content="wrong", tool_call_id="other")],
        [
            assistant("a"),
            ToolMessage(content="a", tool_call_id="a"),
            ToolMessage(content="duplicate", tool_call_id="a"),
        ],
        [assistant("a"), ToolMessage(content="wrong name", tool_call_id="a", name="different")],
    ],
)
def test_ambiguous_tool_history_fails_closed(messages):
    with pytest.raises(ValueError, match="Ambiguous"):
        SessionPersistenceHook._fill_dangling_calls(messages)


@pytest.mark.parametrize("delete", [True, False])
async def test_hook_lifecycle_snapshot_and_explicit_deletion(delete):
    backend = MemoryBackend()
    hook = SessionPersistenceHook(backend, delete_on_session_close=delete)
    await hook.on_start({})
    state = AgentState(
        messages=[HumanMessage(content="hi"), assistant("a")],
        context={"thread_id": "trusted", "tenant_id": "t"},
        tenant_id="t",
    )
    assert await hook.before(state) is state
    with bind_thread_id("trusted"):
        assert await hook.after(state) is state
    assert get_thread_id() is None
    restored = await hook.on_session_load("trusted")
    assert restored["tenant_id"] == "t" and restored["context"] == state["context"]
    assert restored["messages"][-1].content == UNKNOWN_TOOL_RESULT
    await hook.on_session_close("trusted")
    assert (await backend.load("trusted") is None) is delete
    await hook.on_stop()
    await hook.on_stop()


async def test_missing_binding_fails_instead_of_silently_skipping_persistence():
    hook = SessionPersistenceHook(MemoryBackend())
    await hook.on_start({})
    with pytest.raises(RuntimeError, match="thread_id scope"):
        await hook.after(AgentState(messages=[]))
    await hook.on_stop()


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"version": 2, "messages": []},
        {"version": 1, "messages": "bad"},
        {"version": 1, "messages": [], "context": []},
    ],
)
async def test_malformed_or_future_snapshot_is_rejected(data):
    backend = MemoryBackend()
    hook = SessionPersistenceHook(backend)
    await hook.on_start({})
    await backend.save("s", data)
    with pytest.raises(ValueError):
        await hook.on_session_load("s")
    await hook.on_stop()


async def test_capability_factory_and_entry_point_config_initialization():
    capability = SessionPersistenceCapability()
    capability.initialize(
        {"backend": "memory", "retention_days": 7, "delete_on_session_close": True}
    )
    assert capability.name == "session_persistence" and capability.tools() == []
    assert capability.config.retention_days == 7 and capability.hooks()[0].delete_on_session_close
    assert isinstance(get_capability({}), SessionPersistenceCapability)
    await capability.close()  # assembly rollback before hook start
    await capability.hooks()[0].on_start({})
    await capability.hooks()[0].on_stop()
    await capability.close()


@pytest.mark.parametrize(
    "data",
    [{"backend": "unknown"}, {"retention_days": 0}, {"sqlite_path": ""}, {"permissions": []}],
)
def test_config_rejects_invalid_or_unknown_values(data):
    with pytest.raises(ValueError):
        SessionPersistenceConfig.model_validate(data)


def test_disabled_and_memory_plugins_do_not_import_optional_drivers():
    code = """
import sys
from capabilities.session_persistence.capability import get_capability
get_capability({})
assert 'aiosqlite' not in sys.modules and 'asyncpg' not in sys.modules
assert 'capabilities.session_persistence.backends.sqlite' not in sys.modules
assert 'capabilities.session_persistence.backends.postgres' not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True)

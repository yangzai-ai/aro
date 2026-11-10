"""The typed kernel view preserves legacy context and identity semantics."""

from typing import Any

import pytest
from langchain_core.messages import HumanMessage

from capabilities.memory.identity import memory_namespace
from core.kernel.loop import KernelLoop
from core.kernel.state import AgentState, RuntimeContext, runtime_context
from core.kernel.trace import TraceHook
from tests.test_kernel_loop import FakeLLMProvider, FakeTool


def test_read_of_absent_context_does_not_mutate_state():
    state = AgentState(messages=[])
    assert runtime_context(state) == {}
    assert "context" not in state
    created = runtime_context(state, create=True)
    assert created is state["context"]
    created["thread_id"] = "first"
    assert runtime_context(state) is created
    assert state["context"]["thread_id"] == "first"


def test_legacy_mapping_unknown_keys_and_object_references_survive():
    payload = {"nested": [1, 2]}
    legacy: dict[str, Any] = {"thread_id": "existing", "custom": payload, "user_id": None}
    state = AgentState(messages=[], context=legacy, tenant_id="top")
    context = runtime_context(state, create=True)
    context["trace_id"] = "trace"
    assert state["context"] is legacy and context is legacy
    assert legacy["custom"] is payload
    assert "extra" not in legacy and "tenant_id" not in legacy
    assert legacy["user_id"] is None and state["tenant_id"] == "top"
    assert memory_namespace(state) == ("aro", "l2", "top", "thread", "existing")


def test_internal_typed_context_retains_all_fields_and_none_identity():
    state = AgentState(messages=[])
    context = runtime_context(state, create=True)
    context.update(
        RuntimeContext(
            thread_id="thread",
            tenant_id=None,
            user_id=None,
            trace_id="trace",
            tool_call_id="tool",
            parent_thread_id="parent",
            delegation_depth=1,
            extra={"custom": "value"},
        )
    )
    assert runtime_context(state) is context
    assert memory_namespace(state) == ("aro", "l2", "local", "thread", "thread")


@pytest.mark.parametrize("identity", ["", "   ", 42, False])
def test_invalid_legacy_identity_remains_invalid(identity):
    legacy: dict[str, Any] = {"thread_id": "valid-thread", "user_id": identity}
    state = AgentState(messages=[], context=legacy)
    runtime_context(state, create=True)["trace_id"] = "trace"
    assert legacy["user_id"] is identity
    with pytest.raises(ValueError, match="trusted user_id or thread_id"):
        memory_namespace(state)


def test_conflicting_tenants_still_rejected():
    state = AgentState(
        messages=[], tenant_id="one", context={"tenant_id": "two", "user_id": "alice"}
    )
    assert runtime_context(state)["tenant_id"] == "two"
    with pytest.raises(ValueError, match="Conflicting tenant"):
        memory_namespace(state)


async def test_loop_and_trace_write_through_the_original_mapping():
    marker = {"unchanged": True}
    legacy: dict[str, Any] = {"thread_id": "thread", "unknown": marker}
    state = AgentState(messages=[HumanMessage(content="hello")], context=legacy)
    trace = TraceHook()
    loop = KernelLoop(
        FakeLLMProvider("", [{"id": "call", "name": "fake_tool", "args": {}}]),
        [FakeTool()],
        [trace],
    )
    result = await loop.run_once(state)
    assert result["context"] is legacy
    assert legacy["unknown"] is marker
    recorded = trace.get_latest_trace()
    assert legacy["trace_id"] == recorded.trace_id
    assert legacy["tool_call_id"] == recorded.tool_calls[0].call_id == "call"
    assert recorded.thread_id == "thread" and recorded.tool_calls[0].success

"""Uniform handles, real engine nesting, LangGraph dispatch and scoped events."""

import asyncio
import json
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import yaml
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from capabilities.multiagent.capability import MultiAgentCapability
from capabilities.multiagent.handle import AgentContext, AgentHandle, AgentResult, LocalAgentHandle
from capabilities.multiagent.registry import AgentRegistry
from capabilities.multiagent.supervisor import DelegationInput, Supervisor
from core.config_loader import AgentConfig, LLMConfig
from core.kernel.engine import AgentEngine
from core.kernel.state import AgentState
from core.llm.provider import LLMResponse
from tests.test_kernel_loop import FakeLLMProvider


class WireHandle:
    """A non-Local implementation proving the boundary is transport-independent.

    JSON round trips imitate a transport boundary without adding HTTP/A2A code.
    """

    name = "arbitrary_domain"
    description = "一个动态登记的专项领域，没有总控中的硬编码关键词"

    def __init__(self):
        self.calls = []

    async def invoke(self, task, context):
        context = AgentContext.model_validate_json(context.model_dump_json())
        self.calls.append((task, context))
        result = AgentResult(agent_name=self.name, answer="wire result", trace={"tool_calls": []})
        return AgentResult.model_validate_json(result.model_dump_json())


def test_registry_catalogue_validation_and_lookup():
    registry = AgentRegistry()
    handle = WireHandle()
    assert isinstance(handle, AgentHandle)
    registry.register(handle)
    assert registry.get(handle.name) is handle
    assert registry.catalogue()[0].description == handle.description
    with pytest.raises(ValueError, match="Duplicate"):
        registry.register(handle)
    with pytest.raises(KeyError):
        registry.get("missing")
    with pytest.raises(TypeError):
        registry.register(object())
    bad = WireHandle()
    bad.name = "bad name"
    with pytest.raises(ValueError):
        registry.register(bad)
    with pytest.raises(ValueError):
        MultiAgentCapability({}, registry=AgentRegistry())


async def test_uniform_handle_through_graph_and_dynamic_tool_description():
    handle = WireHandle()
    registry = AgentRegistry()
    registry.register(handle)
    capability = MultiAgentCapability({}, registry=registry)
    assert capability.tools() and capability.middlewares()
    assert handle.description in capability.delegate_tool.description
    assert capability.delegate_tool.schema["properties"]["agent_name"]["enum"] == [handle.name]
    identity = AgentContext(thread_id="thread", user_id="trusted", tenant_id="tenant")
    result = await Supervisor(registry).invoke(
        DelegationInput(agent_name=handle.name, task="some task"), identity
    )
    assert result.answer == "wire result"
    assert handle.calls[0][1] == identity


def runtime_state(text="hello", thread="thread-a", user="alice"):
    return AgentState(
        messages=[HumanMessage(content=text)],
        context={"thread_id": thread, "user_id": user, "tenant_id": "tenant"},
    )


async def test_budget_identity_validation_and_model_cannot_forge_context():
    handle = WireHandle()
    registry = AgentRegistry()
    registry.register(handle)
    cap = MultiAgentCapability({}, registry=registry)
    assert not (await cap.delegate_tool.run(agent_name=handle.name, task="x")).success
    state = runtime_state()
    await cap.middleware.before(state)
    assert not (
        await cap.delegate_tool.run(agent_name=handle.name, task="x", user_id="bob")
    ).success
    state["context"]["tool_call_id"] = "parent"
    await cap.middleware.before_tool("delegate_agent", {}, state)
    result = await cap.delegate_tool.run(agent_name=handle.name, task="x")
    assert result.success
    assert handle.calls[0][1].user_id == "alice"
    assert handle.calls[0][1].parent_call_id == "parent"
    await cap.middleware.before(state)  # another model subround, same turn
    assert not (await cap.delegate_tool.run(agent_name=handle.name, task="x")).success
    state["messages"].append(AIMessage(content="done"))
    await cap.middleware.after(state)
    assert not (await cap.delegate_tool.run(agent_name=handle.name, task="x")).success
    await cap.middleware.before(runtime_state(thread="new"))
    assert (await cap.delegate_tool.run(agent_name=handle.name, task="x")).success
    nested = runtime_state()
    nested["context"]["delegation_depth"] = 1
    await cap.middleware.before(nested)
    assert not (await cap.delegate_tool.run(agent_name=handle.name, task="x")).success


@pytest.mark.parametrize("failure", ["raise", "timeout", "identity", "unknown", "unsuccessful"])
async def test_failures_are_bounded_and_do_not_expose_exceptions(failure, caplog):
    class Broken(WireHandle):
        async def invoke(self, task, context):
            if failure == "timeout":
                await asyncio.sleep(10)
            if failure == "identity":
                return AgentResult(agent_name="wrong", answer="wrong")
            if failure == "unsuccessful":
                return AgentResult(agent_name=self.name, answer="", success=False, error="No data")
            raise RuntimeError("PRIVATE-KEY-SENTINEL")

    registry = AgentRegistry()
    registry.register(Broken())
    cap = MultiAgentCapability({"timeout_seconds": 0.01}, registry=registry)
    await cap.middleware.before(runtime_state())
    result = await asyncio.wait_for(
        cap.delegate_tool.run(
            agent_name="missing" if failure == "unknown" else "arbitrary_domain", task="x"
        ),
        0.5,
    )
    assert not result.success
    assert "PRIVATE" not in str(result.error) + caplog.text


class ScriptedProvider(FakeLLMProvider):
    """Deterministic route decisions for tests only; production routes via LLM."""

    def __init__(self, role="parent", target="helpdesk_data"):
        super().__init__("")
        self.role, self.target, self.seen = role, target, []

    async def chat(self, messages, tools=None, **kwargs):
        self.seen.append(list(messages))
        if isinstance(messages[-1], ToolMessage):
            result = json.loads(messages[-1].content)
            if self.role == "parent":
                return LLMResponse(content="总控整合：" + result["answer"])
            return LLMResponse(content=f"共 {result['count']} 个待更新工单（模拟数据）")
        if self.role == "parent":
            return LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "same-id",
                        "name": "delegate_agent",
                        "args": {"agent_name": self.target, "task": str(messages[-1].content)},
                    }
                ],
            )
        return LLMResponse(
            content="",
            tool_calls=[
                {"id": "same-id", "name": "query_tickets", "args": {"status": "open"}}
            ],
        )

    async def chat_stream(self, messages, tools=None, **kwargs):
        response = await self.chat(messages, tools)
        if response.tool_calls:
            call = response.tool_calls[0]
            yield {
                "tool_calls_delta": [
                    {
                        "index": 0,
                        "id": call["id"],
                        "function": {"name": call["name"], "arguments": json.dumps(call["args"])},
                    }
                ]
            }
            yield {"finish_reason": "tool_calls"}
        else:
            for char in response.content:
                yield {"delta": char}
            yield {"finish_reason": "stop"}


def make_engine(tmp_path, role="parent", target="helpdesk_data"):
    path = tmp_path / f"{role}.yml"
    config = (
        {"capabilities": []}
        if role == "parent"
        else {
            "capabilities": ["helpdesk_tools"],
            "capability_config": {"helpdesk_tools": {"module": "projects.helpdesk.capability"}},
            "tools": ["query_tickets"],
        }
    )
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    engine = AgentEngine(
        LLMConfig(OPENAI_API_KEY="unused", OPENAI_API_BASE="https://unused.test"), AgentConfig(path)
    )
    engine.loop.llm = ScriptedProvider(role, target)
    return engine


def parent_factory(tmp_path, target="helpdesk_data", hold=None, cleanup=None, child_records=None):
    @asynccontextmanager
    async def child_scope():
        child = make_engine(tmp_path, "child")
        if child_records is not None:
            child_records.append(child)
        try:
            if hold is not None:
                hold.set()
                await asyncio.Event().wait()
            yield child
        finally:
            await child.llm._client.close()
            if cleanup is not None:
                cleanup.set()

    registry = AgentRegistry()
    registry.register(LocalAgentHandle(target, "查询模拟业务数据", child_scope))
    cap = MultiAgentCapability({}, registry=registry)
    parent = make_engine(tmp_path, target=target)
    # Test injection through the same public KernelLoop assembly inputs.
    parent.capabilities.append(cap)
    parent.tools.extend(cap.tools())
    parent.loop.tools = parent.tools
    parent.loop._tool_map.update({tool.name: tool for tool in cap.tools()})
    parent.loop._available_tools.extend(cap.tools())
    parent.middlewares.extend(cap.middlewares())
    return parent


@pytest.mark.parametrize("stream", [False, True])
async def test_full_nested_engine_trace_events_and_no_shared_state(tmp_path, stream):
    children = []
    engine = parent_factory(tmp_path, child_records=children)
    events = []

    async def receive(event):
        events.append(event)

    state = runtime_state("今天有几个待更新工单？")
    original_context = dict(state["context"])
    try:
        result = await engine.run("今天有几个待更新工单？", state, stream=stream, on_event=receive)
        assert "2" in result["messages"][-1].content
        trace = engine.trace_middleware.get_latest_trace().to_dict()
        delegated = trace["tool_calls"][0]
        assert delegated["tool_name"] == "delegate_agent"
        assert delegated["result"]["agent_name"] == "helpdesk_data"
        child_trace = delegated["result"]["trace"]
        assert child_trace["tool_calls"][0]["tool_name"] == "query_tickets"
        assert child_trace["thread_id"] != original_context["thread_id"]
        child_messages = children[0].loop.llm.seen[0]
        assert len(child_messages) == 2  # own system + delegated task, no parent history
        assert original_context.items() <= result["context"].items()
        child_calls = [
            event for event in events if event.type == "tool_call" and event.data.get("agent_name")
        ]
        assert child_calls[0].data["tool_name"] == "helpdesk_data/query_tickets"
        assert child_calls[0].data["parent_call_id"] == "same-id"
        assert child_calls[0].data["call_id"] != "same-id"
        assert any(event.type == "agent_token" for event in events)
        prose = "".join(event.data["delta"] for event in events if event.type == "token")
        assert prose == (result["messages"][-1].content if stream else "")
    finally:
        await engine.llm._client.close()


async def test_cancellation_cleans_child_and_outer_sink_is_restored(tmp_path):
    entered, closed = asyncio.Event(), asyncio.Event()
    engine = parent_factory(tmp_path, hold=entered, cleanup=closed)
    events = []

    async def receive(event):
        events.append(event)

    task = asyncio.create_task(engine.run("query", runtime_state(), stream=True, on_event=receive))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
    assert engine.trace_middleware.current_trace is None
    await engine.llm._client.close()


async def test_context_and_events_do_not_cross_concurrent_parents(tmp_path):
    first = parent_factory(tmp_path)
    second = parent_factory(tmp_path)
    logs = [[], []]

    async def run(engine, index):
        async def receive(event):
            logs[index].append(event)

        return await engine.run(
            "query",
            runtime_state(thread=f"thread-{index}", user=f"user-{index}"),
            stream=True,
            on_event=receive,
        )

    try:
        await asyncio.gather(run(first, 0), run(second, 1))
        assert all(log for log in logs)
        ids = [
            {
                event.data["call_id"]
                for event in log
                if event.type == "tool_call" and event.data.get("agent_name")
            }
            for log in logs
        ]
        assert ids[0].isdisjoint(ids[1])
        assert (
            first.trace_middleware.get_latest_trace().thread_id
            != second.trace_middleware.get_latest_trace().thread_id
        )
    finally:
        await first.llm._client.close()
        await second.llm._client.close()


async def test_local_rejects_nested_invocation_and_write_specialist(tmp_path):
    @asynccontextmanager
    async def child_scope():
        engine = make_engine(tmp_path, "child")
        engine.tools.append(type("WriteTool", (), {"permission": "write"})())
        try:
            yield engine
        finally:
            await engine.llm._client.close()

    handle = LocalAgentHandle("child", "description", child_scope)
    with pytest.raises(ValueError, match="Nested"):
        await handle.invoke("x", AgentContext(thread_id="x", depth=1))
    with pytest.raises(ValueError, match="read-only"):
        await handle.invoke("x", AgentContext(thread_id="x"))


def test_optional_plugin_does_not_import_when_disabled():
    script = """
import sys
from core.config_loader import AgentConfig, LLMConfig
from core.kernel.engine import AgentEngine
engine=AgentEngine(LLMConfig(OPENAI_API_KEY='unused',OPENAI_API_BASE='https://unused.test'),AgentConfig('projects/helpdesk/config/agent_api.yml'))
assert 'delegate_agent' not in [tool.name for tool in engine.tools]
assert not any(name.startswith('capabilities.multiagent') for name in sys.modules)
assert not any(name.startswith('projects.orchestration_demo') for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[3],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr



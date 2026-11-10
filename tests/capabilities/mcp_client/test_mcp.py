"""MCP is a tool source: schemas, permissions, isolation and ordinary execution."""

import asyncio
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool, ToolException

from capabilities.mcp_client.capability import MCPClientCapability
from capabilities.mcp_client.client import ServerConfig, discover
from capabilities.mcp_client.tool_adapter import MCPTool
from core.config_loader import AgentConfig, LLMConfig
from core.kernel.engine import AgentEngine
from core.llm.provider import LLMResponse
from tests.test_kernel_loop import FakeLLMProvider

SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
    "additionalProperties": False,
}


def remote(name="read_file", calls=None, fail=False, hold=None, closed=None):
    async def invoke(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        try:
            if hold is not None:
                hold.set()
                await asyncio.Event().wait()
            if fail:
                raise ToolException("SECRET-SENTINEL")
            return "file contents", SimpleNamespace(structured_content={"size": 13})
        finally:
            if closed is not None:
                closed.set()

    return StructuredTool(
        name=name,
        description="Read file",
        args_schema=SCHEMA,
        coroutine=invoke,
        response_format="content_and_artifact",
    )


def config(**overrides):
    return {"servers": {"fs": {"command": "unused", **overrides}}}


def agent_config(tmp_path, hitl=True):
    path = tmp_path / "agent.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "capabilities": ["mcp_client", "helpdesk_tools"] + (["hitl"] if hitl else []),
                "capability_config": {
                    "mcp_client": config(),
                    "helpdesk_tools": {"module": "projects.helpdesk.capability"},
                    "hitl": {"mode": "cli"},
                },
            }
        ),
        encoding="utf-8",
    )
    return AgentConfig(path)


def llm_settings():
    return LLMConfig(OPENAI_API_KEY="unused", OPENAI_API_BASE="http://unused.test")


async def test_wrapper_schema_artifact_validation_forbidden_and_close():
    calls = []
    tool = MCPTool("fs", remote(calls=calls), "read", 1)
    assert tool.name == "mcp_fs__read_file"
    schema = tool.schema
    schema["required"] = []
    assert tool.schema == SCHEMA
    assert not (await tool.run(path=1)).success
    assert not (await tool.run(path="x", extra=True)).success
    result = await tool.run(path="x")
    assert result.success and result.output["structured_content"] == {"size": 13}
    assert len(calls) == 1
    tool.closed = True
    assert not (await tool.run(path="x")).success
    denied = MCPTool("fs", remote(calls=calls), "forbidden", 1)
    assert not (await denied.run(path="x")).success
    assert len(calls) == 1


async def test_tool_error_timeout_and_cancel_propagate(caplog):
    tool = MCPTool("fs", remote(fail=True), "read", 1)
    result = await tool.run(path="x")
    assert not result.success
    assert "SECRET-SENTINEL" not in result.error + caplog.text
    entered, closed = asyncio.Event(), asyncio.Event()
    tool = MCPTool("fs", remote(hold=entered, closed=closed), "read", 0.02)
    result = await tool.run(path="x")
    assert "timed out" in result.error and closed.is_set()
    closed.clear()
    entered.clear()
    tool.timeout = 5
    task = asyncio.create_task(tool.run(path="x"))
    await entered.wait()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


async def test_discovery_defaults_overrides_and_close(monkeypatch):
    async def fake_discover(server, settings):
        return [remote(), remote("write_file"), remote("delete_file")]

    monkeypatch.setattr("capabilities.mcp_client.capability.discover", fake_discover)
    cap = MCPClientCapability(config(permissions={"read_file": "read", "delete_file": "forbidden"}))
    with pytest.raises(RuntimeError, match="create"):
        cap.tools()
    await cap.start()
    assert [t.permission for t in cap.tools()] == ["read", "write", "forbidden"]
    with pytest.raises(RuntimeError, match="already started"):
        await cap.start()
    tools = cap.tools()
    await cap.close()
    assert not cap.tools() and all(t.closed for t in tools)


async def test_server_failure_timeout_duplicate_isolation(monkeypatch, caplog):
    async def fake_discover(server, settings):
        if server == "bad":
            raise OSError("SECRET-SENTINEL")
        if server == "slow":
            await asyncio.Event().wait()
        if server == "duplicate":
            return [remote(), remote()]
        return [remote()]

    monkeypatch.setattr("capabilities.mcp_client.capability.discover", fake_discover)
    cap = MCPClientCapability(
        {
            "servers": {
                name: {"command": "unused", "discovery_timeout_seconds": 0.02}
                for name in ["bad", "slow", "duplicate", "good", "other"]
            }
        }
    )
    await cap.start()
    assert [t.name for t in cap.tools()] == ["mcp_good__read_file", "mcp_other__read_file"]
    assert "SECRET-SENTINEL" not in caplog.text
    assert all(x in caplog.text for x in ["bad", "slow", "duplicate"])
    await cap.close()


@pytest.mark.parametrize(
    "settings",
    [
        {"servers": {"bad_name": {"command": "x"}}},
        config(permissions={"read_file": "readOnly"}),
        config(transport="http"),
        config(call_timeout_seconds=0),
    ],
)
def test_invalid_config_fails_early(settings):
    with pytest.raises(ValueError):
        MCPClientCapability(settings)


async def test_adapter_discovery_uses_one_server_and_connection(monkeypatch):
    client = SimpleNamespace(get_tools=AsyncMock(return_value=[remote()]))
    factory = __import__("unittest.mock", fromlist=["Mock"]).Mock(return_value=client)
    monkeypatch.setattr("langchain_mcp_adapters.client.MultiServerMCPClient", factory)
    settings = ServerConfig(command="node", args=["server.js"], env={"KEY": "value"})
    tools = await discover("fs", settings)
    assert len(tools) == 1
    factory.assert_called_once_with({"fs": settings.connection()})
    client.get_tools.assert_awaited_once_with(server_name="fs")


class ToolProvider(FakeLLMProvider):
    def __init__(self, name):
        super().__init__("")
        self.name = name

    async def chat(self, messages, tools=None, **kwargs):
        if isinstance(messages[-1], ToolMessage):
            return LLMResponse(content=str(messages[-1].content))
        return LLMResponse(
            content="",
            tool_calls=[
                {
                    "id": "mcp_call",
                    "name": self.name,
                    "args": {"path": "example.txt"},
                }
            ],
        )


@pytest.mark.parametrize("answer,expected", [("y", True), ("n", False)])
async def test_engine_mixed_tools_default_write_hitl_trace(tmp_path, monkeypatch, answer, expected):
    calls = []
    monkeypatch.setattr(
        "capabilities.mcp_client.capability.discover", AsyncMock(return_value=[remote(calls=calls)])
    )
    monkeypatch.setattr("builtins.input", lambda _: answer)
    engine = await AgentEngine.create(llm_settings(), agent_config(tmp_path))
    try:
        names = [t.name for t in engine.tools]
        assert "query_tickets" in names and "mcp_fs__read_file" in names
        engine.loop.llm = ToolProvider("mcp_fs__read_file")
        await engine.run("read")
        trace = engine.trace_hook.get_latest_trace()
        assert trace.tool_calls[0].success is expected
        assert bool(calls) is expected
    finally:
        await engine.close()
    assert engine.llm._client.is_closed


async def test_missing_hitl_blocks_default_write(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "capabilities.mcp_client.capability.discover", AsyncMock(return_value=[remote(calls=calls)])
    )
    engine = await AgentEngine.create(llm_settings(), agent_config(tmp_path, hitl=False))
    try:
        engine.loop.llm = ToolProvider("mcp_fs__read_file")
        state = await engine.run("read")
        assert "HITL" in state["messages"][-1].content and not calls
    finally:
        await engine.close()


def test_sync_constructor_rejects_async_capability(tmp_path):
    with pytest.raises(RuntimeError, match="await AgentEngine.create"):
        AgentEngine(llm_settings(), agent_config(tmp_path))


def test_disabled_import_isolation():
    code = """
import asyncio, sys
from core.config_loader import AgentConfig, LLMConfig
from core.kernel.engine import AgentEngine
async def main():
    engine = await AgentEngine.create(LLMConfig(OPENAI_API_KEY='unused', OPENAI_API_BASE='http://unused.test'), AgentConfig('projects/helpdesk/config/agent_no_knowledge.yml'))
    await engine.close()
asyncio.run(main())
assert not any(name.startswith(('capabilities.mcp_client', 'langchain_mcp_adapters', 'mcp')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


async def test_read_override_skips_hitl_and_forbidden_is_hidden(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "capabilities.mcp_client.capability.discover",
        AsyncMock(return_value=[remote(calls=calls), remote("hidden")]),
    )
    cfg = agent_config(tmp_path)
    cfg._config["capability_config"]["mcp_client"]["servers"]["fs"]["permissions"] = {
        "read_file": "read",
        "hidden": "forbidden",
    }
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("read must not request approval"))
    engine = await AgentEngine.create(llm_settings(), cfg)
    try:
        assert "mcp_fs__hidden" not in {t.name for t in engine.tools}
        engine.loop.llm = ToolProvider("mcp_fs__read_file")
        await engine.run("read")
        assert calls and engine.trace_hook.get_latest_trace().tool_calls[0].success
    finally:
        await engine.close()


async def test_invalid_external_metadata_degrades_server(monkeypatch, caplog):
    invalid = remote("bad/name")
    monkeypatch.setattr(
        "capabilities.mcp_client.capability.discover", AsyncMock(return_value=[invalid])
    )
    cap = MCPClientCapability(config())
    await cap.start()
    assert not cap.tools() and "unavailable" in caplog.text
    await cap.close()


async def test_pydantic_schema_tool_and_tool_error_status():
    from pydantic import BaseModel

    class Input(BaseModel):
        path: str

    tool = remote()
    tool.args_schema = Input
    wrapper = MCPTool("fs", tool, "read", 1)
    assert (await wrapper.run(path="x")).success
    wrapper.remote = SimpleNamespace(
        name="read_file",
        ainvoke=AsyncMock(
            return_value=ToolMessage(content="secret", tool_call_id="x", status="error")
        ),
    )
    result = await wrapper.run(path="x")
    assert not result.success and "secret" not in result.error

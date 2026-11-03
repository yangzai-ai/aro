"""MCP tools use existing SSE approval and trace with an async engine factory."""

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from langchain_core.messages import ToolMessage

from api.app import create_app
from api.settings import APISettings
from core.kernel.engine import AgentEngine
from tests.api.helpers import OpenChat
from tests.capabilities.mcp_client.test_mcp import agent_config, llm_settings, remote
from tests.test_kernel_loop import FakeLLMProvider


class MCPStream(FakeLLMProvider):
    async def chat_stream(self, messages, tools=None, **kwargs):
        if isinstance(messages[-1], ToolMessage):
            yield {"delta": "done"}
            yield {"finish_reason": "stop"}
        else:
            yield {
                "tool_calls_delta": [
                    {
                        "index": 0,
                        "id": "m1",
                        "function": {
                            "name": "mcp_fs__read_file",
                            "arguments": json.dumps({"path": "x"}),
                        },
                    }
                ]
            }
            yield {"finish_reason": "tool_calls"}


@pytest.mark.parametrize("approved", [True, False])
async def test_mcp_api_approval_trace(tmp_path, monkeypatch, approved):
    calls = []
    monkeypatch.setattr(
        "capabilities.mcp_client.capability.discover", AsyncMock(return_value=[remote(calls=calls)])
    )
    config = agent_config(tmp_path)
    config._config["capability_config"]["hitl"]["mode"] = "api"

    async def factory():
        engine = await AgentEngine.create(llm_settings(), config)
        engine.loop.llm = MCPStream("")
        return engine

    app = create_app(APISettings(_env_file=None, token=None), factory)
    async with app.router.lifespan_context(app), OpenChat(app, "read") as stream:
        event = (await stream.until("approval_required"))[-1]
        session_id = event["data"]["thread_id"]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/approve",
                json={
                    "session_id": session_id,
                    "approval_id": event["data"]["approval_id"],
                    "approved": approved,
                },
            )
        assert response.status_code == 202
        events = await stream.until("done")
        step = events[-1]["data"]["trace"]["tool_calls"][0]
        assert step["tool_name"] == "mcp_fs__read_file"
        assert step["success"] is approved
        assert bool(calls) is approved


async def test_disconnect_cancels_mcp_call_and_releases_session(tmp_path, monkeypatch):
    entered, closed = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(
        "capabilities.mcp_client.capability.discover",
        AsyncMock(return_value=[remote(hold=entered, closed=closed)]),
    )
    cfg = agent_config(tmp_path, hitl=False)
    cfg._config["capability_config"]["mcp_client"]["servers"]["fs"]["permissions"] = {
        "read_file": "read"
    }

    async def factory():
        engine = await AgentEngine.create(llm_settings(), cfg)
        engine.loop.llm = MCPStream("")
        return engine

    app = create_app(APISettings(_env_file=None, token=None), factory)
    async with app.router.lifespan_context(app):
        async with OpenChat(app, "read") as stream:
            identity = (await stream.next("session"))["data"]["session_id"]
            await asyncio.wait_for(entered.wait(), 2)
        assert closed.is_set()
        session = app.state.sessions.sessions[identity]
        assert not session.busy and session.task.done()

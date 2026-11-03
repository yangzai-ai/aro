"""Real project expert assembly, owned catalogue and read-only delegation."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from api.settings import APISettings
from capabilities.multiagent.capability import MultiAgentCapability
from capabilities.multiagent.handle import AgentContext
from core.config_loader import AgentConfig, LLMConfig
from core.kernel.engine import AgentEngine
from projects.helpdesk.api import create_app
from tests.api.test_workbench import ScenarioProvider


@pytest.mark.parametrize("enabled", [True, False])
async def test_owned_expert_catalogue_auth_expiry_and_close(monkeypatch, enabled):
    settings = LLMConfig(OPENAI_API_KEY="unused", OPENAI_API_BASE="http://unused.test")
    monkeypatch.setattr("api.sessions.load_llm_config", lambda: settings)
    monkeypatch.setattr(AgentEngine, "_init_llm", lambda self: ScenarioProvider())
    config = "agent_workbench_experts.yml" if enabled else "agent_workbench_light.yml"
    app = create_app(
        APISettings(_env_file=None, config=f"projects/helpdesk/config/{config}", token="secret")
    )
    async with app.router.lifespan_context(app):
        session = await app.state.sessions.reserve(None)
        session.busy = False
        headers = {"authorization": "Bearer secret", "x-aro-session": session.id}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            assert (await client.get("/api/helpdesk/agents")).status_code == 401
            assert (
                await client.get(
                    "/api/helpdesk/agents", headers={**headers, "authorization": "Bearer wrong"}
                )
            ).status_code == 401
            assert (
                await client.get(
                    "/api/helpdesk/agents", headers={**headers, "x-aro-session": "missing"}
                )
            ).status_code == 404
            response = await client.get("/api/helpdesk/agents", headers=headers)
            assert response.status_code == 200
            if enabled:
                assert [item["name"] for item in response.json()] == ["ticket_analyst"]
                assert "独立模拟" in response.json()[0]["description"]
                assert "delegate_agent" in [tool.name for tool in session.engine.tools]
            else:
                assert response.json() == []
            session.updated = time.monotonic() - app.state.settings.session_ttl_seconds - 1
            assert (await client.get("/api/helpdesk/agents", headers=headers)).status_code == 404
            assert session.id not in app.state.sessions.sessions


async def test_example_expert_is_read_only_and_closes_child(monkeypatch):
    clients = []

    def provider(self):
        instance = ScenarioProvider()
        instance._client = SimpleNamespace(close=AsyncMock())
        clients.append(instance._client)
        return instance

    settings = LLMConfig(OPENAI_API_KEY="unused", OPENAI_API_BASE="http://unused.test")
    monkeypatch.setattr("projects.helpdesk.experts.load_llm_config", lambda: settings)
    monkeypatch.setattr(AgentEngine, "_init_llm", provider)
    from projects.helpdesk.experts import get_agent

    handle = get_agent({})
    async with handle.engine_scope() as child:
        assert all(tool.permission == "read" for tool in child.tools)
        assert {tool.name for tool in child.tools} == {
            "query_tickets",
            "get_ticket_history",
            "check_duplicate_tickets",
        }
    clients[0].close.assert_awaited_once()
    result = await handle.invoke("query", AgentContext(thread_id="parent", user_id="alice"))
    assert result.agent_name == "ticket_analyst" and result.success
    assert result.trace["tool_calls"][0]["tool_name"] == "query_tickets"
    clients[1].close.assert_awaited_once()


async def test_catalogue_does_not_leak_between_sessions(monkeypatch):
    settings = LLMConfig(OPENAI_API_KEY="unused", OPENAI_API_BASE="http://unused.test")
    monkeypatch.setattr("api.sessions.load_llm_config", lambda: settings)
    monkeypatch.setattr(AgentEngine, "_init_llm", lambda self: ScenarioProvider())
    app = create_app(
        APISettings(
            _env_file=None, config="projects/helpdesk/config/agent_workbench_light.yml", token=None
        )
    )
    async with app.router.lifespan_context(app):
        one = await app.state.sessions.reserve(None)
        two = await app.state.sessions.reserve(None)
        one.busy = two.busy = False
        one.engine.capabilities.append(
            MultiAgentCapability(
                AgentConfig(
                    "projects/helpdesk/config/agent_workbench_experts.yml"
                ).capability_config["multiagent"]
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            assert (
                len(
                    (
                        await client.get("/api/helpdesk/agents", headers={"x-aro-session": one.id})
                    ).json()
                )
                == 1
            )
            assert (
                await client.get("/api/helpdesk/agents", headers={"x-aro-session": two.id})
            ).json() == []

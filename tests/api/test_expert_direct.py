"""Persistent expert routes retain context without invoking the supervisor."""

import asyncio
import json
import time

import httpx
import pytest
from fastapi import HTTPException
from langchain_core.messages import HumanMessage

from api.settings import APISettings
from core.config_loader import LLMConfig
from core.kernel.engine import AgentEngine
from projects.helpdesk.api import create_app
from tests.api.test_workbench import ScenarioProvider


@pytest.fixture
async def experts_app(monkeypatch):
    settings = LLMConfig(OPENAI_API_KEY="unused", OPENAI_API_BASE="http://unused.test")
    monkeypatch.setattr("api.sessions.load_llm_config", lambda: settings)
    monkeypatch.setattr("projects.helpdesk.experts.load_llm_config", lambda: settings)
    monkeypatch.setattr(AgentEngine, "_init_llm", lambda self: ScenarioProvider())
    app = create_app(APISettings(
        _env_file=None, token=None,
        config="projects/helpdesk/config/agent_workbench_experts.yml",
    ))
    async with app.router.lifespan_context(app):
        yield app


async def chat(client, message, identity=None, target=None):
    response = await client.post("/api/chat", json={
        "message": message, "session_id": identity, "target_agent": target,
    })
    assert response.status_code == 200, response.text
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert "event: error" not in response.text
    return events[-1]


async def test_direct_multiturn_context_trace_and_routing_binding(experts_app):
    app = experts_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        catalogue = await client.get("/api/helpdesk/agents")
        assert catalogue.status_code == 200 and len(catalogue.json()) == 2
        assert not app.state.sessions.sessions  # first screen creates no engines
        main = await chat(client, "query")
        direct = await chat(client, "query", target="ticket_analyst")
        identity = direct["session_id"]
        original = app.state.sessions.sessions[identity]
        again = await chat(client, "query", identity, "ticket_analyst")
        assert again["session_id"] == identity != main["session_id"]
        assert original is app.state.sessions.sessions[identity]
        assert original.state["context"]["thread_id"] != app.state.sessions.sessions[main["session_id"]].state["context"]["thread_id"]
        assert len([m for m in original.state["messages"] if isinstance(m, HumanMessage)]) == 2
        assert len([m for m in app.state.sessions.sessions[main["session_id"]].state["messages"] if isinstance(m, HumanMessage)]) == 1
        assert "delegate_agent" not in [t.name for t in original.engine.tools]
        assert "delegate_agent" in [t.name for t in app.state.sessions.sessions[main["session_id"]].engine.tools]
        assert again["trace"]["tool_calls"][0]["tool_name"] == "query_tickets"
        assert again["trace"]["trace_id"] != main["trace"]["trace_id"]
        for target in (None, "knowledge_expert", "unknown"):
            result = await client.post("/api/chat", json={"message": "query", "session_id": identity, "target_agent": target})
            assert result.status_code == 400
        assert (await client.post("/api/chat", json={"message": "query", "target_agent": "../../config"})).status_code == 400


async def test_expert_busy_capacity_expiry_and_close_once(experts_app):
    store = experts_app.state.sessions
    store.settings.max_sessions = 1
    session = await store.reserve(None, target_agent="ticket_analyst")
    with pytest.raises(HTTPException) as busy:
        await store.reserve(session.id, target_agent="ticket_analyst")
    assert busy.value.status_code == 409
    with pytest.raises(HTTPException) as capacity:
        await store.reserve(None)
    assert capacity.value.status_code == 503
    client = session.engine.llm._client
    session.busy = False
    session.updated = time.monotonic() - store.settings.session_ttl_seconds - 1
    await store.expire()
    assert session.id not in store.sessions
    client.close.assert_awaited_once()
    await asyncio.gather(store.close_session(session), store.close_session(session))
    client.close.assert_awaited_once()


async def test_direct_knowledge_expert_is_read_only_and_peer_lifecycle(experts_app, tmp_path):
    from pathlib import Path

    import yaml

    from projects.helpdesk.experts import get_agent

    cfg = yaml.safe_load(Path("projects/helpdesk/config/agent_knowledge_expert.yml").read_text(encoding="utf-8"))
    cfg["capability_config"]["knowledge"]["qdrant_path"] = ":memory:"
    cfg["capability_config"]["knowledge"]["device"] = "cpu"
    path = tmp_path / "knowledge.yml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    store = experts_app.state.sessions
    store._expert_handles["knowledge_expert"] = get_agent({"name": "knowledge_expert", "config_path": path})
    main = await store.reserve(None)
    ticket = await store.reserve(None, target_agent="ticket_analyst")
    knowledge = await store.reserve(None, target_agent="knowledge_expert")
    assert len(store.sessions) == 3
    assert len({s.state["context"]["thread_id"] for s in (main, ticket, knowledge)}) == 3
    assert all(t.permission == "read" for t in knowledge.engine.tools)
    assert {t.name for t in knowledge.engine.tools} == {"query_tickets", "get_ticket_history", "check_duplicate_tickets", "search_knowledge_base"}
    for session in (main, ticket, knowledge):
        session.busy = False
    async with httpx.AsyncClient(transport=httpx.ASGITransport(experts_app), base_url="http://test") as client:
        result = await client.delete("/api/helpdesk/session", headers={"x-aro-session": knowledge.id})
        assert result.status_code == 200
    assert knowledge.id not in store.sessions and main.id in store.sessions and ticket.id in store.sessions
    knowledge.engine.llm._client.close.assert_awaited_once()
    main.engine.llm._client.close.assert_not_awaited()


async def test_cancelled_close_waiter_does_not_cancel_expert_cleanup(experts_app):
    store = experts_app.state.sessions
    session = await store.reserve(None, target_agent="ticket_analyst")
    session.busy = False
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_close():
        entered.set()
        await release.wait()

    session.engine.llm._client.close.side_effect = slow_close
    first = asyncio.create_task(store.close_session(session))
    await entered.wait()
    second = asyncio.create_task(store.close_session(session))
    first.cancel()
    release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert isinstance(results[0], asyncio.CancelledError) and results[1] is None
    session.engine.llm._client.close.assert_awaited_once()



async def test_expert_failed_start_releases_capacity_and_hides_details(experts_app):
    from contextlib import asynccontextmanager

    store = experts_app.state.sessions
    original = store._expert_handles["ticket_analyst"].engine_scope

    @asynccontextmanager
    async def broken():
        raise RuntimeError("SECRET-CONFIG-SENTINEL")
        yield

    store._expert_handles["ticket_analyst"].engine_scope = broken
    async with httpx.AsyncClient(transport=httpx.ASGITransport(experts_app), base_url="http://test") as client:
        result = await client.post("/api/chat", json={"message": "query", "target_agent": "ticket_analyst"})
        assert result.status_code == 503 and "SECRET" not in result.text
    assert store._creating == 0 and not store.sessions and not store._pending
    store._expert_handles["ticket_analyst"].engine_scope = original
    session = await store.reserve(None, target_agent="ticket_analyst")
    session.busy = False


async def test_direct_rejects_write_expert_and_releases_engine(experts_app):
    from contextlib import asynccontextmanager

    store = experts_app.state.sessions
    engines = []

    @asynccontextmanager
    async def writable():
        engine = await store._create_engine()
        engines.append(engine)
        try:
            yield engine
        finally:
            await engine.close()

    store._expert_handles["ticket_analyst"].engine_scope = writable
    async with httpx.AsyncClient(transport=httpx.ASGITransport(experts_app), base_url="http://test") as client:
        result = await client.post("/api/chat", json={"message": "query", "target_agent": "ticket_analyst"})
        assert result.status_code == 503
    assert not store.sessions and store._creating == 0
    engines[0].llm._client.close.assert_awaited_once()

"""HTTP trace access uses real streamed engines, scoped lookup and redaction."""

import json
import time

import pytest
from pydantic import SecretStr


async def turn(client, message="query", session_id=None):
    response = await client.post("/chat", json={"message": message, "session_id": session_id})
    assert response.status_code == 200
    frames = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    return frames[-1]["session_id"], frames[-1]["trace"]


@pytest.mark.asyncio
async def test_trace_http_scope_retention_and_alias(service):
    app, client = service
    identity, trace = await turn(client)
    headers = {"X-ARO-Session": identity}
    path = f"/traces/{trace['trace_id']}"
    assert (await client.get(path, headers=headers)).json() == trace
    assert (await client.get(f"/sessions/{identity}/traces")).json() == [trace]
    assert (await client.get(f"/api{path}", headers=headers)).json() == trace
    other, _ = await turn(client)
    assert (await client.get(path, headers={"X-ARO-Session": other})).status_code == 404
    assert (await client.get(path)).status_code == 422
    _, newer = await turn(client, session_id=identity)
    assert (await client.get(path, headers=headers)).status_code == 404
    assert (await client.get(f"/sessions/{identity}/traces")).json() == [newer]
    app.state.sessions.sessions[identity].updated = (
        time.monotonic() - app.state.settings.session_ttl_seconds - 1
    )
    assert (await client.get(f"/sessions/{identity}/traces")).status_code == 404


@pytest.mark.asyncio
async def test_trace_auth_redaction_and_pagination(service):
    app, client = service
    identity, trace = await turn(client, "secret")
    app.state.settings.token = SecretStr("DEPLOYMENT-SECRET")
    url = f"/sessions/{identity}/traces"
    assert (await client.get(url)).status_code == 401
    headers = {"Authorization": "Bearer DEPLOYMENT-SECRET", "X-ARO-Session": identity}
    stored = app.state.sessions.sessions[identity].engine.trace_hook.traces[-1]
    stored.user_input = "DEPLOYMENT-SECRET"
    result = await client.get(f"/traces/{trace['trace_id']}", headers=headers)
    assert result.status_code == 200
    assert "BACKEND-SECRET-SENTINEL" not in result.text
    assert "DEPLOYMENT-SECRET" not in result.text
    assert "user:password" not in result.text
    assert "[redacted]" in result.text
    assert (await client.get(url + "?offset=1", headers=headers)).json() == []
    for query in ("limit=0", "limit=101", "offset=-1"):
        assert (await client.get(url + "?" + query, headers=headers)).status_code == 422
    assert (await client.get("/sessions/missing/traces", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_trace_openapi_and_no_current_trace(service):
    app, client = service
    session = await app.state.sessions.reserve(None)
    session.engine.trace_hook.start(session.state)
    trace = session.engine.trace_hook.current_trace
    assert trace is not None
    assert (await client.get(f"/sessions/{session.id}/traces")).json() == []
    assert (
        await client.get(f"/traces/{trace.trace_id}", headers={"X-ARO-Session": session.id})
    ).status_code == 404
    session.busy = False
    schema = (await client.get("/openapi.json")).json()
    assert "/traces/{trace_id}" in schema["paths"]
    assert "/sessions/{session_id}/traces" in schema["paths"]
    assert "TraceView" in schema["components"]["schemas"]

"""SSE ordering, incremental delivery, session state and sanitized errors."""

import asyncio
import json
import time

import pytest
from pydantic import SecretStr

from tests.api.helpers import OpenChat, StreamingProvider


async def test_health_and_validation(service):
    app, client = service
    assert (await client.get("/health")).json() == {"status": "ok"}
    assert (await client.get("/api/health")).status_code == 200
    for data in [
        {"message": ""},
        {"message": "read", "unexpected": "bad"},
        {"message": "x" * 10001},
    ]:
        assert (await client.post("/chat", json=data)).status_code == 422
    assert (
        await client.post("/chat", json={"message": "read", "session_id": "missing"})
    ).status_code == 404


async def test_read_event_order_and_complete_trace(service):
    app, _ = service
    async with OpenChat(app, "read") as stream:
        events = await stream.until("done")
        assert stream.status == 200
        assert b"text/event-stream" in stream.response_headers[b"content-type"]
        assert [e["type"] for e in events[:3]] == ["session", "tool_call", "tool_result"]
        assert sum(e["type"] == "token" for e in events) > 2
        assert not any(e["type"] == "approval_required" for e in events)
        trace = events[-1]["data"]["trace"]
        assert trace["agent_response"] == "".join(
            e["data"]["delta"] for e in events if e["type"] == "token"
        )
        assert trace["tool_calls"][0]["result"]["count"] == 2
        assert trace["tool_calls"][0]["call_id"] == "c1"


async def test_tokens_arrive_before_provider_finishes(service):
    app, _ = service
    entered, release = asyncio.Event(), asyncio.Event()

    class Slow(StreamingProvider):
        async def chat_stream(self, *args, **kwargs):
            yield {"delta": "第一段"}
            entered.set()
            await release.wait()
            yield {"delta": "第二段"}
            yield {"finish_reason": "stop"}

    original = app.state.sessions.factory

    def factory():
        engine = original()
        engine.loop.llm = Slow()
        return engine

    app.state.sessions.factory = factory
    async with OpenChat(app, "hello") as stream:
        await stream.next("session")
        first = await stream.next("token")
        assert first["data"]["delta"] == "第一段" and entered.is_set()
        assert not stream.task.done()
        release.set()
        events = await stream.until("done")
        assert events[-1]["data"]["trace"]["agent_response"] == "第一段第二段"


@pytest.mark.parametrize("question", ["secret", "failure"])
async def test_no_credentials_in_tokens_trace_or_errors(service, question):
    app, _ = service
    async with OpenChat(app, question) as stream:
        await stream.until("done" if question == "secret" else "error")
        text = stream.raw.decode()
        assert "BACKEND-SECRET-SENTINEL" not in text
        assert "user:password" not in text
        if question == "failure":
            assert "turn_failed" in text and "RuntimeError" not in text


async def test_auth_hook_applies_to_both_route_aliases(service):
    app, client = service
    app.state.settings.token = SecretStr("test-access")
    for route in ["/chat", "/api/chat", "/approve", "/api/approve"]:
        assert (await client.post(route, json={})).status_code == 401
    assert (await client.get("/health")).status_code == 200
    async with OpenChat(app, "read", token="test-access") as stream:
        assert (await stream.until("done"))[-1]["type"] == "done"


async def test_session_capacity_and_expiry(service):
    app, client = service
    app.state.settings.max_sessions = 1
    async with OpenChat(app, "read") as stream:
        session_id = (await stream.next("session"))["data"]["session_id"]
        await stream.until("done")
    assert (await client.post("/chat", json={"message": "read"})).status_code == 503
    app.state.sessions.sessions[session_id].updated = (
        time.monotonic() - app.state.sessions.settings.session_ttl_seconds - 1
    )
    await app.state.sessions.expire()
    assert not app.state.sessions.sessions


async def test_message_limit(service):
    app, client = service
    async with OpenChat(app, "read") as stream:
        identity = (await stream.next("session"))["data"]["session_id"]
        await stream.until("done")
    app.state.settings.max_messages = 1
    response = await client.post("/chat", json={"session_id": identity, "message": "read"})
    assert response.status_code == 409


async def test_sse_frames_cannot_be_injected():
    from api.sse import encode_event
    from core.kernel.events import ExecutionEvent

    frame = encode_event(
        ExecutionEvent("token", {"delta": "\n\nevent: done\ndata: broken", "password": "hidden"})
    )
    assert frame.count("\n\n") == 1 and "hidden" not in frame
    assert json.loads(frame.split("data: ", 1)[1])["password"] == "[redacted]"

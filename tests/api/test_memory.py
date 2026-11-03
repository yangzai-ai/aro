"""HTTP sessions share L2 only when a trusted host identity is configured."""

import time
import uuid

import httpx
import pytest

from api.app import create_app
from api.settings import APISettings
from tests.api.helpers import OpenChat
from tests.capabilities.memory.test_memory import make_engine


@pytest.mark.parametrize("user", [None, "trusted-alice"])
async def test_api_new_session_memory_and_identity_boundary(tmp_path, user):
    store_name = str(uuid.uuid4())
    settings = APISettings(_env_file=None, user_id=user, tenant_id="tenant-a", token=None)
    app = create_app(settings, lambda: make_engine(tmp_path, store_name))
    async with app.router.lifespan_context(app):
        async with OpenChat(app, "我负责网络组的支持") as first:
            first_events = await first.until("done")
        first_id = first_events[0]["data"]["session_id"]
        assert not first_events[-1]["data"]["trace"]["tool_calls"]
        app.state.sessions.sessions[first_id].updated = (
            time.monotonic() - app.state.sessions.settings.session_ttl_seconds - 1
        )
        await app.state.sessions.expire()
        assert first_id not in app.state.sessions.sessions
        async with OpenChat(app, "我负责哪个区域？") as second:
            events = await second.until("done")
        second_id = events[0]["data"]["session_id"]
        assert first_id != second_id
        trace = events[-1]["data"]["trace"]
        assert trace["tool_calls"][0]["tool_name"] == "search_memory"
        assert ("网络" in trace["agent_response"]) == (user is not None)
        # Same store, host switches trusted identity: cannot read Alice's facts.
        settings.user_id = "trusted-bob"
        async with OpenChat(app, "我负责哪个区域？") as third:
            other = await third.until("done")
        assert other[-1]["data"]["trace"]["agent_response"] == "[]"
        # Client cannot forge a runtime identity in chat JSON.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/chat", json={"message": "hello", "user_id": "trusted-alice"}
            )
            assert response.status_code == 422

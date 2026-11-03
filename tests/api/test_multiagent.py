"""SSE includes child tools and final nested traces through the existing API."""

import asyncio

from api.app import create_app
from api.settings import APISettings
from tests.api.helpers import OpenChat
from tests.capabilities.multiagent.test_orchestration import parent_factory


async def test_multiagent_sse_events_trace_and_final_answer(tmp_path):
    app = create_app(APISettings(_env_file=None, token=None), lambda: parent_factory(tmp_path))
    async with app.router.lifespan_context(app):
        async with OpenChat(app, "今天有几个待更新工单？") as stream:
            events = await stream.until("done")
        calls = [event for event in events if event["type"] == "tool_call"]
        assert [event["data"]["tool_name"] for event in calls] == [
            "delegate_agent",
            "helpdesk_data/query_tickets",
        ]
        assert calls[1]["data"]["parent_call_id"] == calls[0]["data"]["call_id"]
        done = events[-1]["data"]
        assert done["trace"]["tool_calls"][0]["result"]["trace"]["tool_calls"][0]["success"]
        assert "总控整合" in done["trace"]["agent_response"]
        prose = "".join(event["data"]["delta"] for event in events if event["type"] == "token")
        assert prose == done["trace"]["agent_response"]
        assert any(event["type"] == "agent_token" for event in events)


async def test_disconnect_cleans_nested_task_and_releases_session(tmp_path):
    entered, closed = asyncio.Event(), asyncio.Event()
    app = create_app(
        APISettings(_env_file=None, token=None),
        lambda: parent_factory(tmp_path, hold=entered, cleanup=closed),
    )
    async with app.router.lifespan_context(app):
        async with OpenChat(app, "query") as stream:
            identity = (await stream.next("session"))["data"]["session_id"]
            await asyncio.wait_for(entered.wait(), 1)
        assert closed.is_set()
        session = app.state.sessions.sessions[identity]
        assert not session.busy and session.task.done()

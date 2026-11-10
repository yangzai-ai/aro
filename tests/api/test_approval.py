"""Approval callbacks, timeouts, disconnect cleanup and concurrent sessions."""

import pytest

from tests.api.helpers import OpenChat


@pytest.mark.parametrize("approved", [True, False])
async def test_pending_callback_and_followup_state(service, approved):
    app, client = service
    async with OpenChat(app, "approve") as stream:
        identity = (await stream.next("session"))["data"]["session_id"]
        required = (await stream.until("approval_required"))[-1]["data"]
        assert required["args"] == {"ticket_id": "TKT-001", "status": "resolved"}
        session = app.state.sessions.sessions[identity]
        repo = next(c.repository for c in session.engine.capabilities if c.name == "helpdesk_tools")
        assert repo.tickets[0].status == "open"
        assert (
            await client.post(
                "/approve",
                json={
                    "session_id": "wrong",
                    "approval_id": required["approval_id"],
                    "approved": True,
                },
            )
        ).status_code == 404
        response = await client.post(
            "/approve",
            json={
                "session_id": identity,
                "approval_id": required["approval_id"],
                "approved": approved,
            },
        )
        assert response.status_code == 202
        events = await stream.until("done")
        result = next(e for e in events if e["type"] == "tool_result")
        assert result["data"]["success"] is approved
        assert repo.tickets[0].status == ("resolved" if approved else "open")
    assert not session.approvals.pending and not session.approvals._decisions
    async with OpenChat(app, "read", identity) as followup:
        done = (await followup.until("done"))[-1]["data"]
        assert done["trace"]["tool_calls"][0]["result"]["count"] == (1 if approved else 2)
    assert (
        await client.post(
            "/approve",
            json={"session_id": identity, "approval_id": required["approval_id"], "approved": True},
        )
    ).status_code == 404


async def test_timeout_rejects_and_cleans_pending(service):
    app, client = service
    async with OpenChat(app, "approve") as stream:
        identity = (await stream.next("session"))["data"]["session_id"]
        required = (await stream.until("approval_required"))[-1]["data"]
        events = await stream.until("done")
        assert any(e["type"] == "error" and e["data"]["code"] == "approval_timeout" for e in events)
        assert events[-1]["data"]["trace"]["tool_calls"][0]["success"] is False
    manager = app.state.sessions.sessions[identity].approvals
    assert not manager.pending and not manager._decisions
    assert (
        await client.post(
            "/approve",
            json={"session_id": identity, "approval_id": required["approval_id"], "approved": True},
        )
    ).status_code == 404


async def test_disconnect_cancels_wait_and_releases_session(service):
    app, _ = service
    async with OpenChat(app, "approve") as stream:
        identity = (await stream.next("session"))["data"]["session_id"]
        await stream.until("approval_required")
        session = app.state.sessions.sessions[identity]
        assert session.busy and session.approvals.pending
    assert not session.busy and not session.approvals.pending
    assert session.task.done() and session.engine.trace_hook.current_trace is None
    repo = next(c.repository for c in session.engine.capabilities if c.name == "helpdesk_tools")
    assert repo.tickets[0].status == "open"


async def test_two_sessions_are_isolated_and_same_session_busy(service):
    app, client = service
    async with OpenChat(app, "approve") as first, OpenChat(app, "read") as second:
        first_id = (await first.next("session"))["data"]["session_id"]
        await first.until("approval_required")
        second_done = (await second.until("done"))[-1]["data"]
        assert second_done["session_id"] != first_id
        assert second_done["trace"]["thread_id"] == second_done["session_id"]
        assert not any(
            c["tool_name"] == "update_ticket" for c in second_done["trace"]["tool_calls"]
        )
        assert (
            await client.post("/chat", json={"session_id": first_id, "message": "read"})
        ).status_code == 409


async def test_turn_timeout_cleans_pending(service):
    app, _ = service
    app.state.settings.turn_timeout_seconds = 0.05
    async with OpenChat(app, "approve") as stream:
        identity = (await stream.next("session"))["data"]["session_id"]
        events = await stream.until("error")
        assert events[-1]["data"]["terminal"] is True
    assert not app.state.sessions.sessions[identity].approvals.pending

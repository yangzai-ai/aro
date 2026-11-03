"""Regression checks for the authorized helpdesk implementation repairs."""

import json

import httpx
import pytest
from pydantic import ValidationError

from projects.helpdesk.capability import HelpdeskConfig, create_repository
from projects.helpdesk.repositories.base import Category, Priority, TicketStatus
from projects.helpdesk.repositories.http import HttpRepository
from projects.helpdesk.repositories.mock import MockRepository
from projects.helpdesk.workbench import ReportParameters, Workbench, WorkbenchConfig


async def test_http_repository_wire_mapping_and_updates():
    ticket = (await MockRepository().get_ticket("TKT-001")).model_dump(mode="json")
    seen = []

    async def handle(request):
        seen.append(request)
        path = request.url.path
        if request.method == "PATCH":
            return httpx.Response(
                200, json={"ticket_id": ticket["id"], "updated": json.loads(request.content)}
            )
        if path.endswith("/history"):
            return httpx.Response(
                200,
                json=[
                    {
                        "ticket_id": ticket["id"],
                        "timestamp": ticket["created_at"],
                        "action": "created",
                        "operator": "HTTP operator",
                    }
                ],
            )
        if path.endswith("/missing"):
            return httpx.Response(404)
        return httpx.Response(200, json=ticket if path.endswith("/TKT-001") else [ticket])

    repo = HttpRepository("http://example.test/service/", transport=httpx.MockTransport(handle))
    rows = await repo.query_tickets(
        TicketStatus.open, Priority.high, Category.account, "支持组 & A"
    )
    assert rows[0].id == "TKT-001"
    assert dict(seen[-1].url.params) == {
        "status": "open",
        "priority": "high",
        "category": "account",
        "assignee": "支持组 & A",
    }
    assert (await repo.get_ticket("TKT-001")).requester == ticket["requester"]
    assert await repo.get_ticket("missing") is None
    assert (await repo.update_ticket("TKT-001", TicketStatus.resolved, "支持组", Priority.urgent))[
        "updated"
    ] == {
        "status": "resolved",
        "assignee": "支持组",
        "priority": "urgent",
    }
    assert (await repo.get_ticket_history("TKT-001", 3))[0].operator == "HTTP operator"
    assert dict(seen[-1].url.params) == {"limit": "3"}
    assert (await repo.check_duplicate("邮箱 & 登录", Category.account))[0].id == "TKT-001"
    assert seen[-1].url.path == "/service/tickets/duplicates"
    assert dict(seen[-1].url.params) == {"title": "邮箱 & 登录", "category": "account"}
    assert all(r.url.path.startswith("/service/tickets") for r in seen)


@pytest.mark.parametrize("kind", ["server_error", "malformed", "cancelled"])
async def test_http_repository_does_not_turn_failures_into_empty_facts(kind):
    import asyncio

    async def handle(request):
        if kind == "cancelled":
            raise asyncio.CancelledError
        return (
            httpx.Response(503)
            if kind == "server_error"
            else httpx.Response(200, json={"bad": "shape"})
        )

    class ObservedTransport(httpx.MockTransport):
        closed = False

        async def aclose(self):
            self.closed = True
            await super().aclose()

    transport = ObservedTransport(handle)
    repo = HttpRepository("http://example.test", transport=transport)
    error = {
        "server_error": httpx.HTTPStatusError,
        "malformed": ValidationError,
        "cancelled": asyncio.CancelledError,
    }[kind]
    with pytest.raises(error):
        await repo.query_tickets()
    assert transport.closed, "The request client must close even on failure or cancellation"


def test_production_config_reaches_http_repository():
    repo = create_repository(
        HelpdeskConfig(data_source="production", base_url="http://example.test", timeout=2.5)
    )
    assert isinstance(repo, HttpRepository) and repo.timeout == 2.5
    cap = Workbench(
        WorkbenchConfig(data_source="production", base_url="http://example.test", timeout=3)
    )
    assert isinstance(cap.repository, HttpRepository) and cap.repository.timeout == 3
    with pytest.raises(ValueError, match="base_url"):
        create_repository(HelpdeskConfig(data_source="production"))


async def test_duplicate_evidence_matches_query_and_is_session_local():
    one, two = Workbench(WorkbenchConfig()), Workbench(WorkbenchConfig())
    tool = next(t for t in one.tools() if t.name == "check_duplicate_tickets")
    result = await tool.run(title="无法登录邮箱系统", category="account")
    assert result.success
    panel = await one.panel("TKT-001")
    assert panel["duplicate_check"]["result"] == result.output
    assert panel["duplicate_check"]["observed_at"]
    assert (await one.panel("TKT-002"))["duplicate_check"] is None
    assert (await two.panel("TKT-001"))["duplicate_check"] is None
    # An empty duplicate list is still a successful observation of this exact query.
    result = await tool.run(title="无匹配的邮箱问题", category="account")
    assert result.success and not result.output["duplicates"]
    assert (await one.panel("TKT-001"))["duplicate_check"] == panel["duplicate_check"]
    assert ("无匹配的邮箱问题", "account") in one.duplicates


def test_forged_report_fields_rejected_before_document_rendering():
    with pytest.raises(ValidationError) as caught:
        ReportParameters.model_validate(
            {"ticket_id": "TKT-001", "status": "resolved", "output_path": "escape.docx"}
        )
    assert {e["loc"] for e in caught.value.errors()} == {("status",), ("output_path",)}
    assert all(e["type"] == "extra_forbidden" for e in caught.value.errors())

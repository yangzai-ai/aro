"""HTTP adapter for the existing helpdesk repository contract.

Each request owns its client, so no session or capability lifecycle extension is
needed. HTTP and validation failures propagate instead of becoming empty facts.
"""

from typing import Any
from urllib.parse import quote

import httpx
from pydantic import TypeAdapter

from projects.helpdesk.repositories.base import (
    Category,
    Priority,
    Ticket,
    TicketHistory,
    TicketStatus,
)


class HttpRepository:
    """Read and update helpdesk facts using a configured HTTP service."""

    def __init__(
        self,
        base_url: str,
        timeout: float = 10,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self.transport = transport

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        body: dict[str, str] | None = None,
        missing_ok: bool = False,
    ) -> Any:
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=self.timeout, transport=self.transport
        ) as client:
            response = await client.request(method, path, params=params, json=body)
            if missing_ok and response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()

    async def query_tickets(
        self,
        status: TicketStatus | None = None,
        priority: Priority | None = None,
        category: Category | None = None,
        assignee: str | None = None,
    ) -> list[Ticket]:
        params: dict[str, str | int] = {}
        for name, value in (("status", status), ("priority", priority), ("category", category)):
            if value is not None:
                params[name] = value.value
        if assignee is not None:
            params["assignee"] = assignee
        return TypeAdapter(list[Ticket]).validate_python(
            await self._request("GET", "tickets", params=params)
        )

    async def get_ticket(self, ticket_id: str) -> Ticket | None:
        payload = await self._request(
            "GET", f"tickets/{quote(ticket_id, safe='')}", missing_ok=True
        )
        return Ticket.model_validate(payload) if payload is not None else None

    async def update_ticket(
        self,
        ticket_id: str,
        status: TicketStatus | None = None,
        assignee: str | None = None,
        priority: Priority | None = None,
    ) -> dict[str, Any]:
        body: dict[str, str] = {}
        for name, value in (("status", status), ("priority", priority)):
            if value is not None:
                body[name] = value.value
        if assignee is not None:
            body["assignee"] = assignee
        return TypeAdapter(dict[str, Any]).validate_python(
            await self._request("PATCH", f"tickets/{quote(ticket_id, safe='')}", body=body)
        )

    async def get_ticket_history(self, ticket_id: str, limit: int = 10) -> list[TicketHistory]:
        return TypeAdapter(list[TicketHistory]).validate_python(
            await self._request(
                "GET", f"tickets/{quote(ticket_id, safe='')}/history", params={"limit": limit}
            )
        )

    async def check_duplicate(self, title: str, category: Category) -> list[Ticket]:
        return TypeAdapter(list[Ticket]).validate_python(
            await self._request(
                "GET", "tickets/duplicates", params={"title": title, "category": category.value}
            )
        )

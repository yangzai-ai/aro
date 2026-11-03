"""Ticket query tool."""

from typing import Any

from pydantic import BaseModel, Field

from core.tools.base import BaseTool, PermissionLevel, ToolResult
from projects.helpdesk.repositories.base import Category, HelpdeskRepository, Priority, TicketStatus


class QueryParameters(BaseModel):
    """Optional filters for ticket queries."""

    status: TicketStatus | None = Field(default=None, description="Ticket status filter")
    priority: Priority | None = Field(default=None, description="Priority level filter")
    category: Category | None = Field(default=None, description="Ticket category filter")
    assignee: str | None = Field(default=None, description="Assigned person or team")


class QueryTicketsTool(BaseTool):
    """Query helpdesk tickets without knowing storage details."""

    def __init__(self, repository: HelpdeskRepository) -> None:
        self.repository = repository

    @property
    def name(self) -> str:
        return "query_tickets"

    @property
    def description(self) -> str:
        return "Query IT support tickets by status, priority, category, or assignee"

    @property
    def permission(self) -> PermissionLevel:
        return "read"

    @property
    def usage_prompt(self) -> str:
        return """Query IT support tickets by various filters.

When to use:
- User asks about open tickets
- Need to filter by status, priority, category, or assignee
- Checking ticket lists

When NOT to use:
- For ticket history (use get_ticket_history)
- For duplicate detection (use check_duplicate_tickets)
- For updates (use update_ticket)

Parameters:
- All filters are optional
- Combine filters to narrow results
- Leave unset to get all tickets"""

    @property
    def schema(self) -> type[BaseModel]:
        return QueryParameters

    async def run(self, **kwargs: Any) -> ToolResult:
        """Return count and JSON-serializable tickets."""
        params = QueryParameters.model_validate(kwargs)
        tickets = await self.repository.query_tickets(
            params.status, params.priority, params.category, params.assignee
        )
        return ToolResult(
            success=True,
            output={
                "count": len(tickets),
                "tickets": [item.model_dump(mode="json") for item in tickets],
            },
        )

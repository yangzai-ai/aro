"""Ticket history tool."""

from typing import Any

from pydantic import BaseModel, Field

from core.tools.base import BaseTool, PermissionLevel, ToolResult
from projects.helpdesk.repositories.base import HelpdeskRepository


class HistoryParameters(BaseModel):
    """Parameters for getting ticket history."""

    ticket_id: str = Field(description="The ticket ID")
    limit: int = Field(default=10, description="Maximum number of history entries")


class GetHistoryTool(BaseTool):
    """Retrieve ticket operation history."""

    def __init__(self, repository: HelpdeskRepository) -> None:
        self.repository = repository

    @property
    def name(self) -> str:
        return "get_ticket_history"

    @property
    def description(self) -> str:
        return "Retrieve operation history for a ticket"

    @property
    def permission(self) -> PermissionLevel:
        return "read"

    @property
    def usage_prompt(self) -> str:
        return """Retrieve operation history for a ticket.

When to use:
- User asks about changes or updates to a ticket
- Need to see who modified the ticket
- Checking ticket timeline

When NOT to use:
- For current ticket status (use query_tickets)
- For general ticket search

Parameters:
- ticket_id: The ticket ID to get history for
- limit: Max number of history entries (default 10)"""

    @property
    def schema(self) -> type[BaseModel]:
        return HistoryParameters

    async def run(self, **kwargs: Any) -> ToolResult:
        """Return the history entries."""
        params = HistoryParameters.model_validate(kwargs)
        history = await self.repository.get_ticket_history(params.ticket_id, params.limit)
        return ToolResult(
            success=True,
            output={
                "ticket_id": params.ticket_id,
                "entries": [entry.model_dump(mode="json") for entry in history],
            },
        )

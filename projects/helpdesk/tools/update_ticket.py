"""Ticket update tool."""

from typing import Any

from pydantic import BaseModel, Field

from core.tools.base import BaseTool, PermissionLevel, ToolResult
from projects.helpdesk.repositories.base import HelpdeskRepository, Priority, TicketStatus


class UpdateParameters(BaseModel):
    """Parameters for updating a ticket."""

    ticket_id: str = Field(description="The ticket ID to update")
    status: TicketStatus | None = Field(default=None, description="New status")
    assignee: str | None = Field(default=None, description="New assignee")
    priority: Priority | None = Field(default=None, description="New priority")


class UpdateTicketTool(BaseTool):
    """Update ticket status, assignee, or priority."""

    def __init__(self, repository: HelpdeskRepository) -> None:
        self.repository = repository

    @property
    def name(self) -> str:
        return "update_ticket"

    @property
    def description(self) -> str:
        return "Update ticket status, assignee, or priority"

    @property
    def permission(self) -> PermissionLevel:
        return "write"

    @property
    def usage_prompt(self) -> str:
        return """Update ticket status, assignee, or priority.

When to use:
- User explicitly requests to update a ticket
- Need to change ticket assignment
- Need to escalate or change priority

When NOT to use:
- Just checking ticket status (use query_tickets)
- For reading history (use get_ticket_history)

Parameters:
- ticket_id: Required - the ticket to update
- At least one of status, assignee, or priority must be provided
- This triggers human approval before execution"""

    @property
    def schema(self) -> type[BaseModel]:
        return UpdateParameters

    async def run(self, **kwargs: Any) -> ToolResult:
        """Execute the update and return the result."""
        params = UpdateParameters.model_validate(kwargs)
        result = await self.repository.update_ticket(
            params.ticket_id, params.status, params.assignee, params.priority
        )
        return ToolResult(success=True, output=result)

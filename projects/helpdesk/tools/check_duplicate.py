"""Duplicate ticket detection tool."""

from typing import Any

from pydantic import BaseModel, Field

from core.tools.base import BaseTool, PermissionLevel, ToolResult
from projects.helpdesk.repositories.base import Category, HelpdeskRepository


class DuplicateParameters(BaseModel):
    """Parameters for duplicate detection."""

    title: str = Field(description="Ticket title to check")
    category: Category = Field(description="Ticket category")


class CheckDuplicateTool(BaseTool):
    """Find similar open tickets."""

    def __init__(self, repository: HelpdeskRepository) -> None:
        self.repository = repository

    @property
    def name(self) -> str:
        return "check_duplicate_tickets"

    @property
    def description(self) -> str:
        return "Find similar open tickets by title and category"

    @property
    def permission(self) -> PermissionLevel:
        return "read"

    @property
    def usage_prompt(self) -> str:
        return """Find similar open tickets to avoid duplicates.

When to use:
- Before creating a new ticket
- User mentions similar issue may exist

When NOT to use:
- For searching historical tickets (use query_tickets)
- For checking specific ticket status

Parameters:
- title: The ticket title to search
- category: The category to search within"""

    @property
    def schema(self) -> type[BaseModel]:
        return DuplicateParameters

    async def run(self, **kwargs: Any) -> ToolResult:
        """Return potential duplicates."""
        params = DuplicateParameters.model_validate(kwargs)
        duplicates = await self.repository.check_duplicate(params.title, params.category)
        return ToolResult(
            success=True,
            output={
                "query": {"title": params.title, "category": params.category.value},
                "duplicates": [ticket.model_dump(mode="json") for ticket in duplicates],
            },
        )

"""Repository contract for IT helpdesk operations."""

from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, Field


class TicketStatus(str, Enum):  # noqa: UP042 - preserve existing str(Enum) behavior
    """Ticket lifecycle status."""

    open = "open"
    assigned = "assigned"
    in_progress = "in_progress"
    resolved = "resolved"
    closed = "closed"


class Priority(str, Enum):  # noqa: UP042 - preserve existing str(Enum) behavior
    """Ticket priority level."""

    low = "low"
    medium = "medium"
    high = "high"
    urgent = "urgent"


class Category(str, Enum):  # noqa: UP042 - preserve existing str(Enum) behavior
    """Ticket category."""

    hardware = "hardware"
    software = "software"
    network = "network"
    account = "account"
    other = "other"


class Ticket(BaseModel):
    """IT support ticket."""

    id: str
    title: str
    description: str
    category: Category
    priority: Priority
    status: TicketStatus
    requester: str
    assignee: str | None = None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None


class TicketHistory(BaseModel):
    """Ticket operation history entry."""

    ticket_id: str
    timestamp: datetime
    action: str
    operator: str
    details: dict[str, Any] = Field(default_factory=dict)


class HelpdeskRepository(Protocol):
    """Business data interface independent of storage."""

    async def query_tickets(
        self,
        status: TicketStatus | None = None,
        priority: Priority | None = None,
        category: Category | None = None,
        assignee: str | None = None,
    ) -> list[Ticket]:
        """Query tickets by filters."""
        ...

    async def get_ticket(self, ticket_id: str) -> Ticket | None:
        """Get a single ticket by ID."""
        ...

    async def update_ticket(
        self,
        ticket_id: str,
        status: TicketStatus | None = None,
        assignee: str | None = None,
        priority: Priority | None = None,
    ) -> dict[str, Any]:
        """Update ticket fields."""
        ...

    async def get_ticket_history(self, ticket_id: str, limit: int = 10) -> list[TicketHistory]:
        """Retrieve ticket operation history."""
        ...

    async def check_duplicate(self, title: str, category: Category) -> list[Ticket]:
        """Find similar open tickets."""
        ...

"""Project capability assembling repository-backed helpdesk tools."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from core.kernel.capability import Capability
from core.kernel.middleware import Middleware
from core.tools.base import BaseTool
from projects.helpdesk.repositories.base import HelpdeskRepository
from projects.helpdesk.repositories.mock import MockRepository
from projects.helpdesk.tools.check_duplicate import CheckDuplicateTool
from projects.helpdesk.tools.get_history import GetHistoryTool
from projects.helpdesk.tools.query_tickets import QueryTicketsTool
from projects.helpdesk.tools.update_ticket import UpdateTicketTool


class HelpdeskConfig(BaseModel):
    """Project-owned data-source selection; the kernel does not interpret it."""

    model_config = ConfigDict(extra="forbid")
    data_source: Literal["mock", "production"] = "mock"
    base_url: str | None = None
    timeout: float = Field(default=10, gt=0)


def create_repository(config: HelpdeskConfig) -> HelpdeskRepository:
    """Select a data source once at assembly time, never inside tools."""
    if config.data_source == "mock":
        return MockRepository()
    from projects.helpdesk.repositories.http import HttpRepository

    if not config.base_url:
        raise ValueError("production data_source requires base_url")
    return HttpRepository(config.base_url, timeout=config.timeout)


class HelpdeskCapability(Capability):
    """Package repository-backed helpdesk tools."""

    def __init__(self, repository: HelpdeskRepository) -> None:
        """Inject the chosen repository into every tool."""
        self.repository = repository
        self._tools: list[BaseTool] = [
            QueryTicketsTool(repository),
            UpdateTicketTool(repository),
            GetHistoryTool(repository),
            CheckDuplicateTool(repository),
        ]

    @property
    def name(self) -> str:
        """Configuration-facing capability name."""
        return "helpdesk_tools"

    def tools(self) -> list[BaseTool]:
        """Return the repository-backed tools."""
        return list(self._tools)

    def middlewares(self) -> list[Middleware]:
        """Approval policy is contributed by the separate HITL capability."""
        return []


def get_capability(config: dict[str, Any]) -> Capability:
    """Standard plugin factory; parse project settings and assemble tools."""
    return HelpdeskCapability(create_repository(HelpdeskConfig.model_validate(config)))

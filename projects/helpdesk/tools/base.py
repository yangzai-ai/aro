"""Base class for helpdesk tools using repository dependency injection."""

from typing import Generic, TypeVar

from pydantic import BaseModel

from core.tools.base import BaseTool
from projects.helpdesk.repositories.base import HelpdeskRepository

T = TypeVar("T", bound=BaseModel)


class ToolParameters(BaseModel):
    """Shared base for strict validation."""


class RepositoryTool(BaseTool, Generic[T]):
    """Helpdesk tool with repository dependency."""

    def __init__(self, repository: HelpdeskRepository) -> None:
        super().__init__()
        self.repository = repository

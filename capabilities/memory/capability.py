"""L2 hybrid capability: one read tool and one automatic write middleware."""

from contextvars import ContextVar
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from capabilities.memory.identity import Namespace
from capabilities.memory.middleware import MemoryWriteMiddleware
from capabilities.memory.store import MemoryStore
from capabilities.memory.tool import SearchMemoryTool
from core.kernel.capability import Capability
from core.kernel.middleware import Middleware
from core.kernel.store import KVStore, shared_store
from core.tools.base import BaseTool


class MemoryConfig(BaseModel):
    """Named stores share within a process; namespaces still isolate identity."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    store_name: str = Field(default="default", min_length=1, max_length=128)
    timeout_seconds: float = Field(default=1, gt=0, le=10)


class MemoryCapability(Capability):
    """An injected KVStore is the persistence replacement point."""

    def __init__(self, config: dict[str, Any], *, backend: KVStore | None = None) -> None:
        self.config = MemoryConfig.model_validate(config)
        self.store = MemoryStore(
            backend if backend is not None else shared_store(self.config.store_name)
        )
        scope: ContextVar[Namespace | None] = ContextVar("memory_namespace", default=None)
        self.search_tool = SearchMemoryTool(self.store, scope, self.config.timeout_seconds)
        self.writer = MemoryWriteMiddleware(self.store, scope, self.config.timeout_seconds)

    @property
    def name(self) -> str:
        return "memory"

    def tools(self) -> list[BaseTool]:
        return [self.search_tool]

    def middlewares(self) -> list[Middleware]:
        return [self.writer]


def get_capability(config: dict[str, Any]) -> Capability:
    """Load only when memory appears in the configured capability list."""
    return MemoryCapability(config)

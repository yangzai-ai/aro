"""Reference standard plugin: lifecycle hooks, no tools or kernel special cases."""

from typing import Any

from core.kernel.capability import Capability
from core.kernel.hook import Hook
from core.tools.base import BaseTool

from .backends import create_backend
from .config import SessionPersistenceConfig
from .hook import SessionPersistenceHook


class SessionPersistenceCapability(Capability):
    def __init__(self, config: SessionPersistenceConfig | None = None) -> None:
        self._configure(config or SessionPersistenceConfig())

    def _configure(self, config: SessionPersistenceConfig) -> None:
        self.config = config
        self.backend = create_backend(self.config)
        self._hook = SessionPersistenceHook(
            self.backend, delete_on_session_close=self.config.delete_on_session_close
        )

    def initialize(self, config: dict[str, Any]) -> None:
        # Entry point class construction invokes this synchronously, before any
        # resource acquisition. The factory below uses the same validated model.
        self._configure(SessionPersistenceConfig.model_validate(config))

    @property
    def name(self) -> str:
        return "session_persistence"

    def tools(self) -> list[BaseTool]:
        return []

    def hooks(self) -> list[Hook]:
        return [self._hook]

    async def close(self) -> None:
        # Covers assembly failures before on_start; backends close idempotently.
        await self.backend.close()


def get_capability(config: dict[str, Any]) -> Capability:
    return SessionPersistenceCapability(SessionPersistenceConfig.model_validate(config))

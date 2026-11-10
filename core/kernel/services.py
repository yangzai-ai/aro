"""Typed host-facing services exposed by hooks, not additional plugin groups."""

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from core.kernel.hook import Hook

if TYPE_CHECKING:
    from core.kernel.engine import AgentEngine


@runtime_checkable
class ApprovalService(Protocol):
    @property
    def mode(self) -> str: ...

    def decide(self, approval_id: str, thread_id: str, approved: bool) -> None: ...


class ExpertInfo(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...


class LocalExpert(Protocol):
    def engine_scope(self) -> AbstractAsyncContextManager["AgentEngine"]: ...


class ExpertDirectory(Protocol):
    def catalogue(self) -> Sequence[ExpertInfo]: ...

    def get(self, name: str) -> LocalExpert: ...


def hook_service(hooks: Sequence[Hook], name: str) -> object | None:
    """Resolve an optional host service; reject ambiguous service ownership."""
    found = [hook.provides[name] for hook in hooks if name in hook.provides]
    if len(found) > 1:
        raise ValueError(f"Multiple hooks provide host service '{name}'")
    return found[0] if found else None

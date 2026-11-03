"""Transport-independent execution events, scoped to the current async turn."""

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExecutionEvent:
    """An execution notification; no HTTP or capability dependency."""

    type: str
    data: dict[str, Any]


EventSink = Callable[[ExecutionEvent], Awaitable[None]]
_sink: ContextVar[EventSink | None] = ContextVar("execution_event_sink", default=None)


@contextmanager
def event_scope(sink: EventSink | None) -> Iterator[None]:
    """Bind an optional sink to this task, and restore it even on cancellation."""
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


async def emit_event(event_type: str, **data: Any) -> None:
    """Publish if a consumer is present; ordinary CLI runs remain silent."""
    sink = _sink.get()
    if sink is not None:
        await sink(ExecutionEvent(event_type, data))

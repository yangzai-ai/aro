"""Task-local trusted execution identity, scoped by the owning engine."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_thread_id: ContextVar[str | None] = ContextVar("aro_thread_id", default=None)


def get_thread_id() -> str | None:
    """Return the current engine invocation identity, or None outside a run."""
    return _thread_id.get()


@contextmanager
def bind_thread_id(thread_id: str) -> Iterator[None]:
    """Bind until scope exit, restoring the parent even on failure/cancellation."""
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError("thread_id must be a non-empty string")
    token = _thread_id.set(thread_id)
    try:
        yield
    finally:
        _thread_id.reset(token)

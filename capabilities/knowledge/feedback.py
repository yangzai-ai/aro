"""Append-only retrieval feedback collection; no optimization or governance."""

import threading
from datetime import UTC, datetime
from pathlib import Path
from weakref import WeakValueDictionary

from pydantic import BaseModel, Field, model_validator

from capabilities.knowledge.workers import drain_thread


class SearchFeedback(BaseModel):
    """A retrieval event with optional user interaction and bounded rating."""

    query: str
    results: list[str]
    user_clicked: int | None = Field(default=None, ge=0)
    user_rating: int | None = Field(default=None, ge=1, le=5)
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @model_validator(mode="after")
    def valid_click(self) -> "SearchFeedback":
        """A click must refer to an actual returned result."""
        if self.user_clicked is not None and self.user_clicked >= len(self.results):
            raise ValueError("user_clicked must refer to an existing result")
        return self


_locks: WeakValueDictionary[str, threading.Lock] = WeakValueDictionary()
_locks_guard = threading.Lock()


class FeedbackCollector:
    """Thread-safe JSONL appends within one process."""

    def __init__(self, feedback_file: Path) -> None:
        """Configure a local feedback destination without creating it yet."""
        self.feedback_file = feedback_file.resolve()
        with _locks_guard:
            self._lock = _locks.setdefault(str(self.feedback_file).casefold(), threading.Lock())

    def collect(self, feedback: SearchFeedback) -> None:
        """Persist one event; write failures propagate to the caller."""
        with self._lock:
            self.feedback_file.parent.mkdir(parents=True, exist_ok=True)
            with self.feedback_file.open("a", encoding="utf-8") as file:
                file.write(feedback.model_dump_json() + "\n")

    async def collect_async(self, feedback: SearchFeedback) -> None:
        """Keep disk writes out of the tool's event loop."""
        await drain_thread(lambda: self.collect(feedback))

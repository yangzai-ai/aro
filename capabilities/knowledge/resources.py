"""Process-local leases for knowledge resources; no conversation objects here."""

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, TypeVar, cast

from anyio import CancelScope

from capabilities.knowledge.embedding.bge_m3 import BGEM3Embedding
from capabilities.knowledge.reranker.bge_reranker import BGEReranker
from capabilities.knowledge.vectordb.qdrant import QdrantVectorDB
from capabilities.knowledge.workers import drain_thread

T = TypeVar("T")
Key = tuple[Any, ...]


@dataclass
class _Entry:
    value: Any
    owners: int = 1
    retiring: Future[None] | None = None


_entries: dict[Key, _Entry] = {}
_lock = threading.Lock()  # Only short dictionary operations, never I/O/inference.


async def acquire(key: Key, factory: Callable[[], T]) -> T:
    """Factories allocate lazy wrappers only. A retiring resource must finish first."""
    while True:
        with _lock:
            entry = _entries.get(key)
            if entry is None:
                value = factory()
                _entries[key] = _Entry(value)
                return value
            if entry.retiring is None:
                entry.owners += 1
                return cast(T, entry.value)
            retiring = entry.retiring
        await asyncio.shield(asyncio.wrap_future(retiring))


async def release(key: Key) -> None:
    """Last owner disposes before removing the key; concurrent acquire waits."""
    with _lock:
        entry = _entries[key]
        entry.owners -= 1
        if entry.owners:
            return
        entry.retiring = Future()
    with CancelScope(shield=True):
        try:
            if isinstance(entry.value, QdrantVectorDB):
                await entry.value.close()
            elif isinstance(entry.value, (BGEM3Embedding, BGEReranker)):

                def unload() -> None:
                    with entry.value._lock:
                        entry.value._model = None

                await drain_thread(unload)
        finally:
            with _lock:
                del _entries[key]
                entry.retiring.set_result(None)


def live_resources() -> int:
    """Diagnostics for lifecycle tests; no keys or credentials are exposed."""
    with _lock:
        return len(_entries)

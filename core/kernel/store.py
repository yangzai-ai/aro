"""Neutral async KV boundary and process-local backend binding.

The default backend searches namespace prefixes. Consumers that require an
exact namespace must retain their own equality filter.
"""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class KVItem:
    """A stored value with its actual namespace, independent of backend types."""

    namespace: tuple[str, ...]
    value: dict[str, Any]


class KVStore(Protocol):
    """Operations needed by consumers; storage lifecycle belongs to the host."""

    async def aput(self, namespace: tuple[str, ...], key: str, value: dict[str, Any]) -> None: ...

    async def asearch(
        self, namespace: tuple[str, ...], *, limit: int = 10, offset: int = 0
    ) -> list[KVItem]: ...


class InMemoryKVStore:
    """Process-local default; backend imports and item conversion stay here."""

    def __init__(self) -> None:
        from langgraph.store.memory import InMemoryStore

        self._backend = InMemoryStore()

    async def aput(self, namespace: tuple[str, ...], key: str, value: dict[str, Any]) -> None:
        await self._backend.aput(namespace, key, value)

    async def asearch(
        self, namespace: tuple[str, ...], *, limit: int = 10, offset: int = 0
    ) -> list[KVItem]:
        items = await self._backend.asearch(namespace, limit=limit, offset=offset)
        return [KVItem(namespace=item.namespace, value=item.value) for item in items]


_stores: dict[str, KVStore] = {}


def shared_store(name: str) -> KVStore:
    """Share a named backend across engines only within the current process."""
    if name not in _stores:
        _stores[name] = InMemoryKVStore()
    return _stores[name]

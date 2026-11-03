"""Fact retrieval over the kernel's neutral KV store boundary."""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from capabilities.memory.identity import Namespace
from core.kernel.store import KVStore


@dataclass(frozen=True)
class MemoryFact:
    """An extracted assertion, not a raw transcript or authoritative knowledge."""

    text: str
    kind: str


def terms(text: str) -> set[str]:
    """Small lexical index: Latin words and overlapping Chinese bigrams."""
    result = set(re.findall(r"[a-z0-9_-]+", text.casefold()))
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        result.update(run[index : index + 2] for index in range(len(run) - 1))
    return result


class MemoryStore:
    """Retrieve facts via a KVStore; persistence lifecycle belongs to the host."""

    def __init__(self, backend: KVStore) -> None:
        self.backend = backend

    async def save(self, namespace: Namespace, fact: MemoryFact) -> None:
        """Append a new fact; deliberately no deduplication or conflict governance."""
        await self.backend.aput(
            namespace,
            str(uuid.uuid4()),
            {"text": fact.text, "kind": fact.kind, "created_at": datetime.now(UTC).isoformat()},
        )

    async def search(self, namespace: Namespace, query: str, limit: int) -> list[dict[str, str]]:
        """Exact-namespace lexical retrieval with bounded output and paged scanning."""
        query_terms = terms(query)
        matches: list[tuple[int, str, dict[str, str]]] = []
        offset = 0
        while True:
            page = await self.backend.asearch(namespace, limit=100, offset=offset)
            for item in page:
                # Backends can use prefix search; never include child namespaces.
                if item.namespace != namespace:
                    continue
                text = item.value.get("text")
                if not isinstance(text, str):
                    continue
                score = len(query_terms & terms(text))
                if score:
                    value = {
                        "text": text,
                        "kind": str(item.value.get("kind", "fact")),
                        "created_at": str(item.value.get("created_at", "")),
                    }
                    matches.append((score, value["created_at"], value))
            if len(page) < 100:
                break
            offset += len(page)
        matches.sort(key=lambda match: (match[0], match[1]), reverse=True)
        return [match[2] for match in matches[:limit]]

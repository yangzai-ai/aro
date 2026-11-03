"""Typed vector-store contract, including complete pagination for BM25."""

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class VectorSearchResult(BaseModel):
    """Stored content with source metadata and optional relevance score."""

    doc_id: str
    content: str
    score: float = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class VectorDocument(VectorSearchResult):
    """Document to be indexed with a dense vector."""

    vector: list[float]


class VectorDB(ABC):
    """Async vector-store interface used by indexing and hybrid retrieval."""

    @abstractmethod
    async def create_collection(self, collection_name: str, dimension: int) -> None:
        """Create or validate a compatible collection."""

    @abstractmethod
    async def upsert(self, collection_name: str, documents: list[VectorDocument]) -> None:
        """Insert/update documents identified by stable IDs."""

    @abstractmethod
    async def search(
        self,
        collection_name: str,
        query_vector: list[float],
        top_k: int = 10,
        filter: dict[str, Any] | None = None,
    ) -> list[VectorSearchResult]:
        """Query nearest neighbours, optionally filtering metadata."""

    @abstractmethod
    async def list_documents(self, collection_name: str) -> list[VectorSearchResult]:
        """Read every document using pagination, never a dummy vector search."""

    @abstractmethod
    async def delete(self, collection_name: str, doc_ids: list[str]) -> None:
        """Remove stale chunks after a successful reindex."""

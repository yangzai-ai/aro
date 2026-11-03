"""Reranking contract."""

from abc import ABC, abstractmethod


class Reranker(ABC):
    """Rank candidates and return original indices with relevance scores."""

    @abstractmethod
    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int = 5,
    ) -> list[tuple[int, float]]:
        """Return indices in descending relevance order."""

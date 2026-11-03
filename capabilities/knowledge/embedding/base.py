"""Embedding contract for local document and query encoders."""

from abc import ABC, abstractmethod


class EmbeddingModel(ABC):
    """Encode documents and queries into the same vector space."""

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Encode a batch, preserving document order."""

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """Encode one query."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Number of coordinates per vector."""

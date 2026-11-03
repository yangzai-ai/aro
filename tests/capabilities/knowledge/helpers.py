"""Deterministic adapters for testing retrieval mechanics without online models."""

from capabilities.knowledge.embedding.base import EmbeddingModel
from capabilities.knowledge.reranker.base import Reranker


class FakeEmbedding(EmbeddingModel):
    """Simple stable vectors; this is not a semantic-quality benchmark."""

    @property
    def dimension(self) -> int:
        return 3

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1, float("Email" in text), float("Network" in text)] for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]


class FakeReranker(Reranker):
    """Pick known corpus terms to expose plumbing/identity errors."""

    async def rerank(
        self, query: str, documents: list[str], top_k: int = 5
    ) -> list[tuple[int, float]]:
        needles = {
            "打印机错误代码": "E05",
            "邮箱密码重置后无法登录": "5 minutes",
            "WiFi频繁掉线怎么办": "update network drivers",
            "打印机卡纸怎么办": "remove jammed paper",
        }
        needle = needles.get(query, query)
        return sorted(
            [(index, float(needle in document)) for index, document in enumerate(documents)],
            key=lambda pair: pair[1],
            reverse=True,
        )[:top_k]

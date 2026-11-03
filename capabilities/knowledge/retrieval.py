"""Dense + Chinese BM25 retrieval, reciprocal-rank fusion, and local reranking."""

import asyncio
import logging
import time
from typing import Any

import jieba
from rank_bm25 import BM25Okapi

from capabilities.knowledge.embedding.base import EmbeddingModel
from capabilities.knowledge.reranker.base import Reranker
from capabilities.knowledge.vectordb.base import VectorDB, VectorSearchResult
from capabilities.knowledge.workers import drain_thread

logger = logging.getLogger(__name__)


def tokenize(text: str) -> list[str]:
    """Keep Chinese search tokens and normalized alphanumerics, removing whitespace."""
    return [
        token.lower()
        for token in jieba.cut_for_search(text)
        if token.strip() and any(c.isalnum() for c in token)
    ]


class HybridRetriever:
    """Rerank the union of dense and keyword candidates, preserving all citations."""

    def __init__(
        self,
        vectordb: VectorDB,
        embedding_model: EmbeddingModel,
        reranker: Reranker,
        collection_name: str = "helpdesk_kb",
        candidate_k: int = 20,
    ) -> None:
        """Inject retrieval adapters; build the BM25 snapshot on first query."""
        self.vectordb = vectordb
        self.embedding_model = embedding_model
        self.reranker = reranker
        self.collection_name = collection_name
        self.candidate_k = candidate_k
        self._documents: dict[str, VectorSearchResult] = {}
        self._ids: list[str] = []
        self._bm25: Any = None
        self._ready = False
        self._build_lock = asyncio.Lock()
        self.last_timings_ms: dict[str, float] = {}

    async def build_bm25_index(self) -> None:
        """Read all Qdrant pages and build a consistent keyword snapshot."""
        async with self._build_lock:
            if self._ready:
                return
            docs = await self.vectordb.list_documents(self.collection_name)

            def build() -> tuple[list[str], Any]:
                corpus = [tokenize(doc.content) or ["__empty__"] for doc in docs]
                return [doc.doc_id for doc in docs], BM25Okapi(corpus) if corpus else None

            self._ids, self._bm25 = await drain_thread(build)
            self._documents = {doc.doc_id: doc for doc in docs}
            self._ready = True

    def _keyword(self, query: str, limit: int) -> list[str]:
        """Return ranked lexical candidates from the same document snapshot."""
        if self._bm25 is None:
            return []
        scores = self._bm25.get_scores(tokenize(query))
        return [
            self._ids[index]
            for index in sorted(
                range(len(scores)),
                key=lambda index: float(scores[index]),
                reverse=True,
            )[:limit]
        ]

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        vector_top_k: int = 20,
        bm25_top_k: int = 20,
    ) -> list[VectorSearchResult]:
        """Fuse ranks using k=60, then rerank bounded candidates by cross-encoder."""
        if not query.strip() or min(top_k, vector_top_k, bm25_top_k, self.candidate_k) < 1:
            raise ValueError("Query must be non-empty and retrieval limits must be positive")
        started = time.perf_counter()
        if not self._ready:
            await self.build_bm25_index()
        if not self._documents:
            return []
        vector = await self.embedding_model.embed_query(query)
        encoded = time.perf_counter()
        dense = await self.vectordb.search(self.collection_name, vector, vector_top_k)
        searched = time.perf_counter()
        keyword = await drain_thread(lambda: self._keyword(query, bm25_top_k))
        lexical = time.perf_counter()
        documents = {**self._documents, **{doc.doc_id: doc for doc in dense}}
        scores: dict[str, float] = {}
        for ranking in ([doc.doc_id for doc in dense], keyword):
            for rank, doc_id in enumerate(ranking, 1):
                scores[doc_id] = scores.get(doc_id, 0) + 1 / (60 + rank)
        candidates = sorted(scores, key=lambda doc_id: (-scores[doc_id], doc_id))[
            : max(top_k, self.candidate_k)
        ]
        ranked = await self.reranker.rerank(
            query, [documents[key].content for key in candidates], top_k
        )
        result = [
            documents[candidates[index]].model_copy(update={"score": score})
            for index, score in ranked
        ]
        finished = time.perf_counter()
        self.last_timings_ms = {
            "prepare_and_embedding": (encoded - started) * 1000,
            "vector_search": (searched - encoded) * 1000,
            "bm25": (lexical - searched) * 1000,
            "fusion_and_rerank": (finished - lexical) * 1000,
            "total": (finished - started) * 1000,
        }
        logger.info("Knowledge retrieval timings_ms=%s", self.last_timings_ms)
        return result

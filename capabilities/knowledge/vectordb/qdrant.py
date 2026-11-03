"""Qdrant local memory/disk adapter using the current query_points API."""

import math
import threading
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient, models

from capabilities.knowledge.vectordb.base import VectorDB, VectorDocument, VectorSearchResult
from capabilities.knowledge.workers import drain_thread


class QdrantVectorDB(VectorDB):
    """Serialize local database operations in worker threads; one owner per disk path."""

    def __init__(
        self,
        path: str = ":memory:",
        *,
        url: str | None = None,
        api_key: str | None = None,
        timeout: float = 15,
    ) -> None:
        """Configure storage; open it lazily on first operation."""
        self.path = path
        self.url = url
        self._api_key = api_key
        self.timeout = timeout
        self._client: QdrantClient | None = None
        self._lock = threading.Lock()

    @property
    def client(self) -> QdrantClient:
        """Open local Qdrant (call only under the adapter's worker-thread lock)."""
        if self._client is None:
            self._client = (
                QdrantClient(
                    url=self.url,
                    api_key=self._api_key,
                    timeout=math.ceil(self.timeout),
                    prefer_grpc=False,
                    check_compatibility=False,
                )
                if self.url
                else (
                    QdrantClient(location=":memory:")
                    if self.path == ":memory:"
                    else QdrantClient(path=self.path)
                )
            )
        return self._client

    @staticmethod
    def _id(doc_id: str) -> str:
        """Translate arbitrary chunk identifiers to valid stable Qdrant UUIDs."""
        return str(uuid5(NAMESPACE_URL, doc_id))

    async def create_collection(self, collection_name: str, dimension: int) -> None:
        """Idempotently create a collection and reject mismatched dimensions."""

        def create() -> None:
            with self._lock:
                if not self.client.collection_exists(collection_name):
                    self.client.create_collection(
                        collection_name,
                        vectors_config=models.VectorParams(
                            size=dimension, distance=models.Distance.COSINE
                        ),
                    )
                else:
                    vectors = self.client.get_collection(collection_name).config.params.vectors
                    if not isinstance(vectors, models.VectorParams) or vectors.size != dimension:
                        raise ValueError(
                            "Collection dimension mismatch; choose a new collection or reindex"
                        )

        await drain_thread(create)

    async def upsert(self, collection_name: str, documents: list[VectorDocument]) -> None:
        """Keep original IDs in payload so BM25 and vector identities always agree."""
        if not documents:
            return

        def insert() -> None:
            with self._lock:
                self.client.upsert(
                    collection_name,
                    points=[
                        models.PointStruct(
                            id=self._id(document.doc_id),
                            vector=document.vector,
                            payload={
                                "doc_id": document.doc_id,
                                "content": document.content,
                                "metadata": document.metadata,
                            },
                        )
                        for document in documents
                    ],
                    wait=True,
                )

        await drain_thread(insert)

    @staticmethod
    def _result(
        point_id: Any, payload: dict[str, Any] | None, score: float = 0
    ) -> VectorSearchResult:
        """Reconstruct typed content and nested metadata without collisions."""
        payload = payload or {}
        return VectorSearchResult(
            doc_id=payload.get("doc_id", str(point_id)),
            content=payload["content"],
            score=score,
            metadata=payload.get("metadata", {}),
        )

    async def search(
        self,
        collection_name: str,
        query_vector: list[float],
        top_k: int = 10,
        filter: dict[str, Any] | None = None,
    ) -> list[VectorSearchResult]:
        """Return nearest neighbours; missing collections produce an indexing instruction."""
        if top_k < 1:
            raise ValueError("top_k must be positive")

        def query() -> list[VectorSearchResult]:
            with self._lock:
                points = self.client.query_points(
                    collection_name,
                    query=query_vector,
                    limit=top_k,
                    with_payload=True,
                    query_filter=models.Filter.model_validate(filter) if filter else None,
                ).points
                return [self._result(point.id, point.payload, point.score) for point in points]

        return await drain_thread(query)

    async def list_documents(self, collection_name: str) -> list[VectorSearchResult]:
        """Scroll all pages, retaining every source for keyword-only matches."""

        def scan() -> list[VectorSearchResult]:
            with self._lock:
                if not self.client.collection_exists(collection_name):
                    raise ValueError(
                        "Knowledge collection missing; run scripts/index_helpdesk_docs.py first"
                    )
                documents: list[VectorSearchResult] = []
                offset: Any = None
                while True:
                    points, offset = self.client.scroll(
                        collection_name,
                        limit=128,
                        offset=offset,
                        with_payload=True,
                        with_vectors=False,
                    )
                    documents.extend(self._result(point.id, point.payload) for point in points)
                    if offset is None:
                        return documents

        return await drain_thread(scan)

    async def delete(self, collection_name: str, doc_ids: list[str]) -> None:
        """Delete obsolete IDs, doing nothing for an empty list."""
        if not doc_ids:
            return

        def remove() -> None:
            with self._lock:
                self.client.delete(
                    collection_name,
                    points_selector=models.PointIdsList(
                        points=[self._id(doc_id) for doc_id in doc_ids],
                    ),
                    wait=True,
                )

        await drain_thread(remove)

    async def close(self) -> None:
        """Release the disk path lock after indexing or a CLI session."""

        def shutdown() -> None:
            with self._lock:
                if self._client is not None:
                    self._client.close()
                    self._client = None

        await drain_thread(shutdown)

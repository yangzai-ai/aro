"""Opt-in actual independent Qdrant service, never replaced by HTTP mocks."""

import os
import uuid

import pytest

from capabilities.knowledge.retrieval import HybridRetriever
from capabilities.knowledge.vectordb.base import VectorDocument
from capabilities.knowledge.vectordb.qdrant import QdrantVectorDB
from tests.capabilities.knowledge.helpers import FakeEmbedding, FakeReranker


@pytest.mark.skipif(
    not os.getenv("ARO_QDRANT_TEST_URL"), reason="Opt-in independent Qdrant service"
)
async def test_real_service_scroll_fusion_rerank_and_sources():
    database = QdrantVectorDB(url=os.environ["ARO_QDRANT_TEST_URL"], timeout=5)
    collection = "v09_acceptance_" + uuid.uuid4().hex
    try:
        await database.create_collection(collection, 3)
        await database.upsert(
            collection,
            [
                VectorDocument(
                    doc_id=str(i),
                    content="分页演示 高度" if i == 139 else f"其他文档{i}",
                    vector=[1, 0, 0],
                    metadata={"source_file": f"{i}.md"},
                )
                for i in range(140)
            ],
        )
        assert len(await database.list_documents(collection)) == 140
        hits = await HybridRetriever(
            database, FakeEmbedding(), FakeReranker(), collection
        ).retrieve("分页演示", top_k=1)
        assert hits[0].metadata["source_file"] == "139.md"
        await database.delete(collection, [str(i) for i in range(140)])
        assert not await database.list_documents(collection)
    finally:
        await database.close()

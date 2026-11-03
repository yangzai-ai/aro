"""Exercise dense/keyword union and source preservation using real local Qdrant."""

from pathlib import Path
from typing import Any

import pytest

from capabilities.knowledge.indexer import index_documents
from capabilities.knowledge.retrieval import HybridRetriever
from capabilities.knowledge.vectordb.base import VectorDocument
from capabilities.knowledge.vectordb.qdrant import QdrantVectorDB
from tests.capabilities.knowledge.helpers import FakeEmbedding, FakeReranker


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("打印机错误代码", "E05"),
        ("邮箱密码重置后无法登录", "5 minutes"),
        ("WiFi频繁掉线怎么办", "update network drivers"),
        ("打印机卡纸怎么办", "remove jammed paper"),
    ],
)
async def test_hybrid_plumbing(query: str, expected: str) -> None:
    """Real corpus with fake neural scores verifies retrieval wiring, not model quality."""
    database = QdrantVectorDB()
    embedding = FakeEmbedding()
    docs = Path(__file__).parents[3] / "projects/helpdesk/docs"
    await index_documents(docs, "docs", embedding, database)
    retriever = HybridRetriever(database, embedding, FakeReranker(), "docs")
    results = await retriever.retrieve(query, top_k=3)
    assert expected in results[0].content
    assert results[0].metadata["source_file"].endswith(".md")
    assert retriever.last_timings_ms["total"] > 0
    await database.close()


async def test_bm25_only_candidates_retain_sources(monkeypatch: Any) -> None:
    """A keyword-only result absent from dense top-k must still reach the final output."""
    database = QdrantVectorDB()
    await database.create_collection("docs", 3)
    await database.upsert(
        "docs",
        [
            VectorDocument(
                doc_id="dense",
                content="unrelated",
                vector=[1, 0, 0],
                metadata={"source_file": "dense.md"},
            ),
            VectorDocument(
                doc_id="keyword",
                content="特别关键词 test",
                vector=[0, 1, 0],
                metadata={"source_file": "keyword.md"},
            ),
            VectorDocument(doc_id="other", content="another text", vector=[0, 0, 1]),
        ],
    )
    retriever = HybridRetriever(database, FakeEmbedding(), FakeReranker(), "docs")

    async def dense_only(*args: Any, **kwargs: Any) -> Any:
        return [next(doc for doc in await database.list_documents("docs") if doc.doc_id == "dense")]

    monkeypatch.setattr(database, "search", dense_only)
    results = await retriever.retrieve("特别关键词", top_k=1)
    assert results[0].doc_id == "keyword"
    assert results[0].metadata["source_file"] == "keyword.md"
    await database.close()


async def test_empty_and_invalid_queries() -> None:
    """Empty collections return no evidence; invalid limits are rejected."""
    database = QdrantVectorDB()
    await database.create_collection("empty", 3)
    retriever = HybridRetriever(database, FakeEmbedding(), FakeReranker(), "empty")
    assert await retriever.retrieve("question") == []
    with pytest.raises(ValueError):
        await retriever.retrieve(" ")
    with pytest.raises(ValueError):
        await retriever.retrieve("question", top_k=0)
    await database.close()

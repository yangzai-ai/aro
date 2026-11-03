"""Real local Qdrant tests (no server, no model download)."""

from pathlib import Path

import pytest

from capabilities.knowledge.vectordb.base import VectorDocument
from capabilities.knowledge.vectordb.qdrant import QdrantVectorDB


async def test_qdrant_crud_pagination_filters_and_disk(tmp_path: Path) -> None:
    """More than one scroll page, idempotency, deletion and reopening all work."""
    database = QdrantVectorDB(str(tmp_path / "vectors"))
    await database.create_collection("docs", 3)
    await database.create_collection("docs", 3)
    with pytest.raises(ValueError, match="dimension"):
        await database.create_collection("docs", 4)
    documents = [
        VectorDocument(
            doc_id=f"arbitrary_chunk_{index}",
            content=f"内容{index}",
            vector=[1, 0, 0],
            metadata={"source_file": "a.md", "group": index % 2},
        )
        for index in range(140)
    ]
    await database.upsert("docs", [])
    await database.upsert("docs", documents)
    await database.upsert("docs", documents)
    assert len(await database.list_documents("docs")) == 140
    hits = await database.search(
        "docs",
        [1, 0, 0],
        top_k=3,
        filter={
            "must": [{"key": "metadata.group", "match": {"value": 1}}],
        },
    )
    assert len(hits) == 3 and all(hit.metadata["group"] == 1 for hit in hits)
    assert hits[0].doc_id.startswith("arbitrary_chunk_")
    await database.delete("docs", [documents[0].doc_id])
    await database.delete("docs", [])
    await database.close()
    reopened = QdrantVectorDB(str(tmp_path / "vectors"))
    assert len(await reopened.list_documents("docs")) == 139
    await reopened.close()


async def test_qdrant_memory_and_missing() -> None:
    """Memory mode uses in-memory storage and missing index failures are readable."""
    database = QdrantVectorDB()
    with pytest.raises(ValueError, match="index"):
        await database.list_documents("missing")
    await database.create_collection("docs", 2)
    with pytest.raises(ValueError):
        await database.search("docs", [1, 0], 0)
    await database.close()

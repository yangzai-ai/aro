"""Corpus synchronization and failed-batch validation."""

from pathlib import Path
from typing import Any

import pytest

from capabilities.knowledge.indexer import index_documents
from capabilities.knowledge.vectordb.qdrant import QdrantVectorDB
from tests.capabilities.knowledge.helpers import FakeEmbedding


async def test_index_reindex_and_remove_stale(tmp_path: Path) -> None:
    """Repeated indexing is idempotent and changed/deleted sources leave no stale hits."""
    first, second = tmp_path / "a.md", tmp_path / "b.md"
    first.write_text("# A\n\nold text", encoding="utf-8")
    second.write_text("# B\n\nremoved", encoding="utf-8")
    database = QdrantVectorDB()
    embedding = FakeEmbedding()
    assert await index_documents(tmp_path, "docs", embedding, database, batch_size=1) == 2
    assert await index_documents(tmp_path, "docs", embedding, database) == 2
    first.write_text("# A\n\nnew text", encoding="utf-8")
    second.unlink()
    assert await index_documents(tmp_path, "docs", embedding, database) == 1
    records = await database.list_documents("docs")
    assert len(records) == 1 and "new text" in records[0].content
    first.unlink()
    with pytest.raises(ValueError, match="No non-empty"):
        await index_documents(tmp_path, "docs", embedding, database)
    assert len(await database.list_documents("docs")) == 1
    await database.close()


async def test_invalid_embedding_batch(tmp_path: Path, monkeypatch: Any) -> None:
    """Do not silently truncate chunks when an encoder returns too few rows."""
    (tmp_path / "a.md").write_text("text", encoding="utf-8")
    embedding = FakeEmbedding()

    async def bad(texts: list[str]) -> list[list[float]]:
        return []

    monkeypatch.setattr(embedding, "embed_documents", bad)
    database = QdrantVectorDB()
    with pytest.raises(ValueError, match="shape"):
        await index_documents(tmp_path, "docs", embedding, database)
    with pytest.raises(ValueError, match="batch_size"):
        await index_documents(tmp_path, "docs", embedding, database, batch_size=0)
    await database.close()

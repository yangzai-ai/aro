"""Markdown splitting bounds, overlap, headings and stable source identities."""

from pathlib import Path

import pytest

from capabilities.knowledge.document_loader import load_and_chunk_documents


def test_document_chunking(tmp_path: Path) -> None:
    """Oversized paragraphs are bounded; body overlap exactly follows configuration."""
    body = "abcdefghijklmnopqrstuvwxyz" * 5
    (tmp_path / "one.md").write_text(f"# 标题\n\n{body}", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub/one.md").write_text("## 另一个\n\n短文", encoding="utf-8")
    chunks = load_and_chunk_documents(tmp_path, chunk_size=50, chunk_overlap=10)
    assert len(chunks) == 4
    assert len({chunk.chunk_id for chunk in chunks}) == 4
    windows = [
        chunk.content.split("\n\n", 1)[1]
        for chunk in chunks
        if chunk.metadata["source_file"] == "one.md"
    ]
    assert all(len(window) <= 50 for window in windows)
    assert windows[0][-10:] == windows[1][:10]
    assert chunks == load_and_chunk_documents(tmp_path, 50, 10)
    assert all(chunk.metadata["heading"] for chunk in chunks)


def test_empty_and_invalid_documents(tmp_path: Path) -> None:
    """Empty/heading-only files do not create duplicate empty chunks."""
    (tmp_path / "empty.md").write_text("# Heading\n\n", encoding="utf-8")
    assert load_and_chunk_documents(tmp_path) == []
    with pytest.raises(ValueError):
        load_and_chunk_documents(tmp_path, 50, 50)
    with pytest.raises(FileNotFoundError):
        load_and_chunk_documents(tmp_path / "missing")

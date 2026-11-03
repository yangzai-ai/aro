"""Markdown paragraph/heading-aware bounded chunks with real overlap."""

import hashlib
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class DocumentChunk(BaseModel):
    """A deterministic chunk identity and citation context."""

    chunk_id: str
    content: str
    metadata: dict[str, Any]


def load_and_chunk_documents(
    docs_dir: Path,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
) -> list[DocumentChunk]:
    """Split sections near paragraph boundaries; cap even a single huge paragraph.

    Heading prefixes carry section context. Size/overlap refer to body characters;
    heading characters are additional. Sources are relative to docs_dir, and a
    stable content hash makes reruns idempotent without same-stem collisions.
    """
    if chunk_size < 1 or not 0 <= chunk_overlap < chunk_size:
        raise ValueError("Require chunk_size > 0 and 0 <= chunk_overlap < chunk_size")
    if not docs_dir.is_dir():
        raise FileNotFoundError(f"Document directory not found: {docs_dir}")
    chunks: list[DocumentChunk] = []
    for file in sorted(docs_dir.rglob("*.md")):
        source = file.relative_to(docs_dir).as_posix()
        text = file.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        sections: list[tuple[str, str]] = []
        heading = ""
        lines: list[str] = []
        for line in text.splitlines():
            if re.match(r"^#{1,6}\s+", line):
                if "\n".join(lines).strip():
                    sections.append((heading, "\n".join(lines).strip()))
                heading = line.strip()
                lines = []
            else:
                lines.append(line)
        if "\n".join(lines).strip():
            sections.append((heading, "\n".join(lines).strip()))
        index = 0
        for heading, body in sections:
            start = 0
            while start < len(body):
                end = min(start + chunk_size, len(body))
                if end < len(body):
                    boundary = body.rfind("\n\n", start + chunk_size // 2, end)
                    if boundary > start + chunk_overlap:
                        end = boundary
                window = body[start:end].strip()
                if window:
                    content = f"{heading}\n\n{window}" if heading else window
                    digest = hashlib.sha256(f"{source}\0{index}\0{content}".encode()).hexdigest()
                    chunks.append(
                        DocumentChunk(
                            chunk_id=digest,
                            content=content,
                            metadata={
                                "source_file": source,
                                "chunk_index": index,
                                "heading": heading,
                            },
                        )
                    )
                    index += 1
                if end == len(body):
                    break
                start = max(start + 1, end - chunk_overlap)
    return chunks

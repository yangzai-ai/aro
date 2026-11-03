"""Synchronize a Markdown corpus into a local vector collection."""

import asyncio
import logging
from pathlib import Path

from capabilities.knowledge.document_loader import load_and_chunk_documents
from capabilities.knowledge.embedding.base import EmbeddingModel
from capabilities.knowledge.vectordb.base import VectorDB, VectorDocument

logger = logging.getLogger(__name__)


async def index_documents(
    docs_dir: Path,
    collection_name: str,
    embedding_model: EmbeddingModel,
    vectordb: VectorDB,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    batch_size: int = 12,
) -> int:
    """Upsert stable chunks in batches; prune stale chunks only after success.

    One collection belongs to one corpus. An empty directory is rejected, to
    prevent accidental deletion after a misconfigured path. Changed documents
    replace their prior chunks; unchanged chunks never accumulate duplicates.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    chunks = await asyncio.to_thread(load_and_chunk_documents, docs_dir, chunk_size, chunk_overlap)
    if not chunks:
        raise ValueError("No non-empty Markdown documents found; existing index was not changed")
    await vectordb.create_collection(collection_name, embedding_model.dimension)
    old = await vectordb.list_documents(collection_name)
    for offset in range(0, len(chunks), batch_size):
        batch = chunks[offset : offset + batch_size]
        vectors = await embedding_model.embed_documents([chunk.content for chunk in batch])
        if len(vectors) != len(batch) or any(
            len(vector) != embedding_model.dimension for vector in vectors
        ):
            raise ValueError("Embedding batch shape mismatch; stale chunks were not deleted")
        await vectordb.upsert(
            collection_name,
            [
                VectorDocument(
                    doc_id=chunk.chunk_id,
                    content=chunk.content,
                    metadata=chunk.metadata,
                    vector=vector,
                )
                for chunk, vector in zip(batch, vectors, strict=True)
            ],
        )
    current_ids = {chunk.chunk_id for chunk in chunks}
    await vectordb.delete(
        collection_name, [doc.doc_id for doc in old if doc.doc_id not in current_ids]
    )
    logger.info("Indexed %d chunks into %s", len(chunks), collection_name)
    return len(chunks)

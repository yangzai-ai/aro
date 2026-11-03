"""Opt-in real model test; ARO_RUN_MODEL_TESTS=1 pytest -q .../test_real_models.py."""

import os
from pathlib import Path

import pytest

from capabilities.knowledge.embedding.bge_m3 import BGEM3Embedding
from capabilities.knowledge.indexer import index_documents
from capabilities.knowledge.reranker.bge_reranker import BGEReranker
from capabilities.knowledge.retrieval import HybridRetriever
from capabilities.knowledge.vectordb.qdrant import QdrantVectorDB

pytestmark = pytest.mark.skipif(
    os.getenv("ARO_RUN_MODEL_TESTS") != "1", reason="Opt-in neural model integration test"
)


async def test_real_embedding_reranker_and_retrieval() -> None:
    """Model dimensions, scalar/batch rerank and four real-corpus quality checks."""
    device = os.getenv("ARO_MODEL_DEVICE", "cpu")
    embedding = BGEM3Embedding(device=device)
    reranker = BGEReranker(device=device)
    vectors = await embedding.embed_documents(["这是第一篇文档", "这是第二篇文档"])
    assert len(vectors) == 2 and len(vectors[0]) == 1024
    assert len(await embedding.embed_query("查询文本")) == 1024
    assert (await reranker.rerank("邮箱登录", ["邮箱登录"]))[0][0] == 0
    database = QdrantVectorDB()
    try:
        await index_documents(
            Path(__file__).parents[3] / "projects/helpdesk/docs", "docs", embedding, database
        )
        retriever = HybridRetriever(database, embedding, reranker, "docs")
        for query, expected in [
            ("打印机错误代码", "E05"),
            ("工单要提前多久", "24 小时"),
            ("禁飞区有哪些", "5 公里"),
            ("时空冲突怎么解决", "调整时间"),
        ]:
            assert expected in (await retriever.retrieve(query, top_k=3))[0].content
    finally:
        await database.close()

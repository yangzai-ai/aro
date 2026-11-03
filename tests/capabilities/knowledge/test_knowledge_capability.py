"""Capability assembly and end-to-end tool invocation with fake neural adapters."""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from capabilities.knowledge.capability import KnowledgeCapability, get_capability
from capabilities.knowledge.indexer import index_documents
from capabilities.knowledge.vectordb.qdrant import QdrantVectorDB
from core.config_loader import AgentConfig, LLMConfig
from core.kernel.engine import AgentEngine
from core.llm.provider import LLMResponse
from projects.helpdesk.main import complete_turn
from tests.capabilities.knowledge.helpers import FakeEmbedding, FakeReranker


async def test_assembly() -> None:
    """Construction configures but does not load neural models or open Qdrant."""
    capability = get_capability({"qdrant_path": ":memory:", "device": "cpu"})
    assert isinstance(capability, KnowledgeCapability)
    assert capability.name == "knowledge" and capability.middlewares() == []
    await capability.start()
    assert capability.tools()[0].name == "search_knowledge_base"
    assert capability.embedding_model._model is None
    assert capability.vectordb._client is None
    await capability.close()
    with pytest.raises(ValueError):
        KnowledgeCapability({"chunk_size": 10, "chunk_overlap": 20})
    with pytest.raises(ValidationError):
        KnowledgeCapability({"unknown": True})


async def test_agent_tool_integration(tmp_path: Path, monkeypatch: Any) -> None:
    """The unchanged engine and tool loop carry citations back to the model."""
    database = QdrantVectorDB(str(tmp_path / "db"))
    await index_documents(
        Path(__file__).parents[3] / "projects/helpdesk/docs", "docs", FakeEmbedding(), database
    )
    await database.close()
    monkeypatch.setattr(
        "capabilities.knowledge.capability.BGEM3Embedding", lambda *args: FakeEmbedding()
    )
    monkeypatch.setattr(
        "capabilities.knowledge.capability.BGEReranker", lambda *args: FakeReranker()
    )
    provider = AsyncMock()
    provider.chat.side_effect = [
        LLMResponse(
            content="查阅知识库",
            tool_calls=[
                {
                    "id": "search",
                    "name": "search_knowledge_base",
                    "args": {"query": "打印机错误代码", "top_k": 1},
                }
            ],
        ),
        LLMResponse(content="打印机错误 E05 表示卡纸，来源：common_issues.md；仅为支持知识库样本。"),
    ]
    monkeypatch.setattr(AgentEngine, "_init_llm", lambda self: provider)
    path = tmp_path / "agent.yml"
    path.write_text(
        f"capabilities: [knowledge]\ncapability_config:\n  knowledge:\n    collection_name: docs\n    qdrant_path: {database.path}\n",
        encoding="utf-8",
    )
    engine = await AgentEngine.create(
        LLMConfig(OPENAI_API_KEY="fake", OPENAI_API_BASE="https://unused.test"), AgentConfig(path)
    )
    state = await complete_turn(engine, "打印机错误代码", None)
    assert "common_issues.md" in str(state["messages"][-2].content)
    assert "E05" in str(state["messages"][-2].content)
    assert "知识库样本" in str(state["messages"][-1].content)
    capability = engine.capabilities[0]
    assert isinstance(capability, KnowledgeCapability)
    await engine.close()

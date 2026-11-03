"""Tool validation, source-bearing output, error handling and feedback."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from capabilities.knowledge.feedback import FeedbackCollector
from capabilities.knowledge.tool import SearchKnowledgeTool
from capabilities.knowledge.vectordb.base import VectorSearchResult


async def test_tool_and_feedback(tmp_path: Path) -> None:
    """Returned evidence includes source/heading and logs original document identities."""
    retriever = Mock()
    retriever.retrieve = AsyncMock(
        return_value=[
            VectorSearchResult(
                doc_id="chunk",
                content="E05 演示规则",
                score=0.9,
                metadata={"source_file": "safety.md", "heading": "Height"},
            )
        ]
    )
    path = tmp_path / "feedback.jsonl"
    tool = SearchKnowledgeTool(retriever, FeedbackCollector(path))
    result = await tool.run(query="最大高度", top_k=1)
    assert result.success and result.output["results"][0]["source"] == "safety.md"
    assert tool.permission == "read" and tool.name == "search_knowledge_base"
    assert (
        "何时不用" in tool.usage_prompt and tool.schema.model_json_schema()["properties"]["query"]
    )
    assert json.loads(path.read_text(encoding="utf-8"))["results"] == ["chunk"]
    assert not (await tool.run(query=" ")).success
    assert not (await tool.run(query="q", top_k=0)).success
    assert not (await tool.run(query="q", top_k=21)).success
    assert not (await tool.run(query="q", unknown=True)).success
    retriever.retrieve = AsyncMock(side_effect=ValueError("missing index"))
    assert not (await tool.run(query="q")).success
    retriever.retrieve = AsyncMock(return_value=[])
    assert (await tool.run(query="q")).output["results"] == []


async def test_feedback_io_failure_does_not_discard_evidence() -> None:
    """A feedback disk error is logged without replacing a valid retrieval result."""
    retriever = Mock(retrieve=AsyncMock(return_value=[]))
    collector = Mock(collect_async=AsyncMock(side_effect=OSError("disk full")))
    assert (await SearchKnowledgeTool(retriever, collector).run(query="q")).success

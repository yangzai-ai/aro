"""Reranker API and single-document scalar output handling."""

import sys
from types import SimpleNamespace
from typing import Any

import pytest

from capabilities.knowledge.reranker.bge_reranker import BGEReranker


async def test_reranker_adapter(monkeypatch: Any) -> None:
    """Scores map to original candidate indices, not the input order."""

    class Model:
        def __init__(self, name: str, **kwargs: Any) -> None:
            assert kwargs == {"use_fp16": False, "devices": "cpu"}

        def compute_score(self, pairs: list[list[str]], **kwargs: Any) -> Any:
            assert pairs[0][0] == "query" and kwargs["normalize"]
            return 0.9 if len(pairs) == 1 else [0.1, 0.9, 0.4]

    monkeypatch.setitem(sys.modules, "FlagEmbedding", SimpleNamespace(FlagReranker=Model))
    monkeypatch.setattr(
        "capabilities.knowledge.reranker.bge_reranker.resolve_model_path", lambda name: name
    )
    reranker = BGEReranker(device="cpu")
    assert await reranker.rerank("query", []) == []
    assert await reranker.rerank("query", ["one"]) == [(0, 0.9)]
    assert await reranker.rerank("query", ["a", "b", "c"], top_k=2) == [(1, 0.9), (2, 0.4)]
    with pytest.raises(ValueError):
        await reranker.rerank("query", [], top_k=0)
    with pytest.raises(ValueError):
        BGEReranker(max_length=0)


async def test_mismatched_scores() -> None:
    """Reject missing scores instead of corrupting the citation alignment."""
    reranker = BGEReranker()
    reranker._model = SimpleNamespace(compute_score=lambda *args, **kwargs: [])
    with pytest.raises(ValueError, match="number of scores"):
        await reranker.rerank("query", ["one"])

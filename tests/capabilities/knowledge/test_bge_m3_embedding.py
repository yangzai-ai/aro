"""BGE adapter batching, fp precision, dimensions and asynchronous execution."""

import asyncio
import sys
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from capabilities.knowledge.embedding.bge_m3 import BGEM3Embedding


@pytest.mark.parametrize("device", ["cpu", "cuda"])
async def test_embedding_adapter(monkeypatch: Any, device: str) -> None:
    """Fake third-party model verifies the real adapter's FlagEmbedding API use."""
    calls: list[Any] = []
    main_thread = threading.get_ident()

    class Model:
        def __init__(self, name: str, **kwargs: Any) -> None:
            assert threading.get_ident() != main_thread
            assert kwargs == {"devices": device, "use_fp16": device == "cuda"}
            calls.append(name)

        def encode(self, texts: list[str], **kwargs: Any) -> dict[str, Any]:
            assert kwargs == {"batch_size": 12, "max_length": 512}
            return {"dense_vecs": SimpleNamespace(tolist=lambda: [[0.5] * 1024 for _ in texts])}

    monkeypatch.setitem(sys.modules, "FlagEmbedding", SimpleNamespace(BGEM3FlagModel=Model))
    monkeypatch.setattr(
        "capabilities.knowledge.embedding.bge_m3.resolve_model_path", lambda name: name
    )
    model = BGEM3Embedding(device=device)
    assert model._model is None
    assert await model.embed_documents([]) == []
    assert len(await model.embed_documents(["第一篇", "第二篇"])) == 2
    vectors = await asyncio.gather(model.embed_query("查询"), model.embed_query("另一个"))
    assert all(len(vector) == model.dimension == 1024 for vector in vectors)
    assert calls == ["BAAI/bge-m3"]


async def test_embedding_shape_errors() -> None:
    """Malformed model output is rejected before indexing."""
    model = BGEM3Embedding(device="cpu")
    model._model = SimpleNamespace(
        encode=lambda *args, **kwargs: {"dense_vecs": SimpleNamespace(tolist=lambda: [[1]])}
    )
    with pytest.raises(ValueError, match="shape"):
        await model.embed_query("test")
    with pytest.raises(ValueError):
        BGEM3Embedding(batch_size=0)

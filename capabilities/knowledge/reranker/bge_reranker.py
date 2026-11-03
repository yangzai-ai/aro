"""Lazy BGE-Reranker-v2-M3 adapter running outside the event loop."""

import threading
from typing import Any

from capabilities.knowledge.model_files import resolve_model_path
from capabilities.knowledge.reranker.base import Reranker
from capabilities.knowledge.workers import drain_thread


class BGEReranker(Reranker):
    """Local cross-encoder with normalized relevance scores."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        device: str = "cuda",
        batch_size: int = 12,
        max_length: int = 512,
    ) -> None:
        """Store settings; model loading is delayed until first rerank."""
        if batch_size < 1 or max_length < 1:
            raise ValueError("batch_size and max_length must be positive")
        self.model_name = model_name
        self.device = device
        self.batch_size = batch_size
        self.max_length = max_length
        self._model: Any = None
        self.load_count = 0
        self._lock = threading.Lock()

    def _score(self, query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]:
        """Load and score with a lock because model inference is shared."""
        with self._lock:
            if self._model is None:
                from FlagEmbedding import FlagReranker

                self._model = FlagReranker(
                    resolve_model_path(self.model_name),
                    use_fp16=self.device.startswith("cuda"),
                    devices=self.device,
                )
                self.load_count += 1
            scores = self._model.compute_score(
                [[query, document] for document in documents],
                normalize=True,
                batch_size=self.batch_size,
                max_length=self.max_length,
            )
            if isinstance(scores, (int, float)):
                scores = [scores]
            if len(scores) != len(documents):
                raise ValueError("Reranker returned a different number of scores than candidates")
            return sorted(
                [(index, float(score)) for index, score in enumerate(scores)],
                key=lambda item: item[1],
                reverse=True,
            )[:top_k]

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int = 5,
    ) -> list[tuple[int, float]]:
        """Handle empty and single-document batches consistently."""
        if top_k < 1:
            raise ValueError("top_k must be positive")
        return await drain_thread(lambda: self._score(query, documents, top_k)) if documents else []

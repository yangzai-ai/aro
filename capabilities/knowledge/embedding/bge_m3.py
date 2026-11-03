"""BGE-M3 dense embeddings with lazy, serialized worker-thread inference."""

import threading
from typing import Any

from capabilities.knowledge.embedding.base import EmbeddingModel
from capabilities.knowledge.model_files import resolve_model_path
from capabilities.knowledge.workers import drain_thread


class BGEM3Embedding(EmbeddingModel):
    """Load BGE locally on first use; CPU uses fp32, CUDA uses fp16."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        device: str = "cuda",
        batch_size: int = 12,
        max_length: int = 512,
    ) -> None:
        """Configure inference without importing torch or downloading weights."""
        if batch_size < 1 or max_length < 1:
            raise ValueError("batch_size and max_length must be positive")
        self.model_name = model_name
        self.device = device
        self.batch_size = batch_size
        self.max_length = max_length
        self._model: Any = None
        self.load_count = 0
        self._lock = threading.Lock()

    @property
    def dimension(self) -> int:
        """BGE-M3 dense vectors have 1024 coordinates."""
        return 1024

    def _encode(self, texts: list[str]) -> list[list[float]]:
        """Serialize loading/inference outside the event loop."""
        with self._lock:
            if self._model is None:
                from FlagEmbedding import BGEM3FlagModel

                self._model = BGEM3FlagModel(
                    resolve_model_path(self.model_name),
                    use_fp16=self.device.startswith("cuda"),
                    devices=self.device,
                )
                self.load_count += 1
            encoded = self._model.encode(
                texts,
                batch_size=self.batch_size,
                max_length=self.max_length,
            )["dense_vecs"].tolist()
            vectors = [[float(value) for value in row] for row in encoded]
            if len(vectors) != len(texts) or any(len(row) != self.dimension for row in vectors):
                raise ValueError("Embedding output shape does not match BGE-M3 (1024)")
            return vectors

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return dense vectors while allowing other async tasks to run."""
        return await drain_thread(lambda: self._encode(texts)) if texts else []

    async def embed_query(self, text: str) -> list[float]:
        """Encode a query using the same model as the document index."""
        return (await self.embed_documents([text]))[0]

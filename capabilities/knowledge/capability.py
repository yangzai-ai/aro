"""Configuration-driven knowledge capability assembly; no core changes required."""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from capabilities.knowledge import resources
from capabilities.knowledge.embedding.bge_m3 import BGEM3Embedding
from capabilities.knowledge.feedback import FeedbackCollector
from capabilities.knowledge.reranker.bge_reranker import BGEReranker
from capabilities.knowledge.retrieval import HybridRetriever
from capabilities.knowledge.tool import SearchKnowledgeTool
from capabilities.knowledge.vectordb.qdrant import QdrantVectorDB
from core.kernel.capability import Capability
from core.kernel.lifecycle import wait_for_cleanup
from core.kernel.middleware import Middleware
from core.tools.base import BaseTool, ToolResult


class KnowledgeConfig(BaseModel):
    """Paths are relative to the working directory, shared by CLI and index script."""

    model_config = ConfigDict(extra="forbid")
    collection_name: str = Field(default="helpdesk_kb", min_length=1)
    qdrant_path: str | None = None
    qdrant_url: str | None = None
    qdrant_api_key_env: str | None = None
    qdrant_timeout_seconds: float = Field(default=15, gt=0, le=120)

    @model_validator(mode="after")
    def storage(self) -> "KnowledgeConfig":
        if self.qdrant_url:
            url = urlsplit(self.qdrant_url)
            if (
                self.qdrant_path is not None
                or url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError("Use either a local path or a credential-free HTTP(S) Qdrant URL")
        elif self.qdrant_api_key_env:
            raise ValueError("Qdrant API key requires remote storage")
        elif self.qdrant_path is None:
            self.qdrant_path = "./data/qdrant"
        if self.qdrant_path == "":
            raise ValueError("Qdrant path cannot be empty")
        return self

    def database(self) -> QdrantVectorDB:
        key = os.environ.get(self.qdrant_api_key_env) if self.qdrant_api_key_env else None
        if self.qdrant_api_key_env and not key:
            raise ValueError("Configured Qdrant API key environment variable is missing")
        return QdrantVectorDB(
            str(Path(self.qdrant_path).resolve())
            if self.qdrant_path and self.qdrant_path != ":memory:"
            else ":memory:",
            url=self.qdrant_url,
            api_key=key,
            timeout=self.qdrant_timeout_seconds,
        )

    device: str = "cuda"
    embedding_model: str = "models/knowledge/bge-m3"
    reranker_model: str = "models/knowledge/bge-reranker-v2-m3"
    docs_dir: Path = Path("projects/helpdesk/docs")
    chunk_size: int = Field(default=500, ge=1)
    chunk_overlap: int = Field(default=50, ge=0)
    batch_size: int = Field(default=12, ge=1)
    max_length: int = Field(default=512, ge=1)
    feedback_file: Path | None = None


class KnowledgeCapability(Capability):
    """Package one read-only tool and its local retrieval infrastructure."""

    def __init__(self, config: dict[str, Any]) -> None:
        """Configure lazy models; first search performs actual loading."""
        self.config = KnowledgeConfig.model_validate(config)
        if self.config.chunk_overlap >= self.config.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        self._keys: list[resources.Key] = []
        self._closing = False
        self._active = 0
        self._idle = asyncio.Event()
        self._idle.set()
        self._close_task: asyncio.Task[None] | None = None
        self.search_tool: SearchKnowledgeTool | None = None

    async def start(self) -> None:
        if self.search_tool is not None or self._closing:
            raise RuntimeError("Knowledge capability already started or closed")
        cfg = self.config

        def model_key(name: str) -> str:
            return os.path.normcase(str(Path(name).resolve())) if Path(name).is_dir() else name

        common = (cfg.device, cfg.batch_size, cfg.max_length)
        try:
            embedding_key = ("embedding", model_key(cfg.embedding_model), *common)
            self.embedding_model = await resources.acquire(
                embedding_key,
                lambda: BGEM3Embedding(
                    cfg.embedding_model,
                    *common,
                ),
            )
            self._keys.append(embedding_key)
            reranker_key = ("reranker", model_key(cfg.reranker_model), *common)
            self.reranker = await resources.acquire(
                reranker_key,
                lambda: BGEReranker(
                    cfg.reranker_model,
                    *common,
                ),
            )
            self._keys.append(reranker_key)
            db_key: resources.Key
            if cfg.qdrant_url:
                key = os.environ.get(cfg.qdrant_api_key_env) if cfg.qdrant_api_key_env else None
                if cfg.qdrant_api_key_env and not key:
                    raise ValueError("Configured Qdrant API key environment variable is missing")
                # Secret remains only in this private registry key, never repr/log output.
                db_key = ("remote", cfg.qdrant_url.rstrip("/"), key, cfg.qdrant_timeout_seconds)
            elif cfg.qdrant_path == ":memory:":
                # Memory stores have no physical shared identity; preserve old isolated semantics.
                db_key = ("memory", object())
            else:
                db_key = ("local", os.path.normcase(str(Path(cfg.qdrant_path or "").resolve())))
            self.vectordb = await resources.acquire(db_key, cfg.database)
            self._keys.append(db_key)
            self.retriever = HybridRetriever(
                self.vectordb,
                self.embedding_model,
                self.reranker,
                cfg.collection_name,
            )
            collector = FeedbackCollector(cfg.feedback_file) if cfg.feedback_file else None
            self.search_tool = _OwnedSearchTool(self, self.retriever, collector)
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close_resources())
        await wait_for_cleanup(self._close_task)

    async def _close_resources(self) -> None:
        interrupted = False
        while not self._idle.is_set():
            try:
                await self._idle.wait()
            except asyncio.CancelledError:
                interrupted = True
                logging.getLogger(__name__).warning(
                    "Knowledge cleanup interrupted (CancelledError)"
                )
        while self._keys:
            try:
                await resources.release(self._keys.pop())
            except asyncio.CancelledError:
                interrupted = True
                logging.getLogger(__name__).warning(
                    "Knowledge cleanup interrupted (CancelledError)"
                )
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Knowledge cleanup failed (%s)", type(exc).__name__
                )
        if interrupted:
            raise RuntimeError("Knowledge cleanup was interrupted; resources were drained")

    @property
    def name(self) -> str:
        """Plugin configuration identifier."""
        return "knowledge"

    def tools(self) -> list[BaseTool]:
        """Only the search tool is exposed to the kernel."""
        if self.search_tool is None:
            raise RuntimeError("Knowledge requires await AgentEngine.create(...) before tools()")
        return [self.search_tool]

    def middlewares(self) -> list[Middleware]:
        """RAG is model-selected, not forced middleware."""
        return []


class _OwnedSearchTool(SearchKnowledgeTool):
    def __init__(
        self,
        owner: KnowledgeCapability,
        retriever: HybridRetriever,
        collector: FeedbackCollector | None,
    ) -> None:
        super().__init__(retriever, collector)
        self.owner = owner

    async def run(self, **kwargs: Any) -> ToolResult:
        owner = self.owner
        if owner._closing:
            return ToolResult(False, error="Knowledge capability is closed")
        owner._active += 1
        owner._idle.clear()
        try:
            return await super().run(**kwargs)
        finally:
            owner._active -= 1
            if not owner._active:
                owner._idle.set()


def get_capability(config: dict[str, Any]) -> Capability:
    """Plugin loader factory using the unchanged core contract."""
    return KnowledgeCapability(config)

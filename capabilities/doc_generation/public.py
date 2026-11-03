"""Typed project composition surface, approved by v1.0 composition decision.

No project, other capability, HTTP, or conversation dependency belongs here.
"""

import asyncio
import logging
import secrets
import shutil
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field

from core.kernel.lifecycle import wait_for_cleanup

T = TypeVar("T")


class DocumentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    templates: dict[str, Path]
    output_dir: Path = Path("data/reports")
    ttl_seconds: float = Field(default=1800, gt=0, le=86400)


class RenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    data: dict[str, str] = Field(max_length=40)


class Artifact(BaseModel):
    id: str
    filename: str
    media_type: str = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    created_at: datetime
    expires_at: datetime
    size: int


class ArtifactContent(BaseModel):
    metadata: Artifact
    content: bytes


class DocumentService:
    """One owner namespace, bounded lifetime; all disk operations under one lock."""

    def __init__(self, config: DocumentConfig) -> None:
        self.config = config
        self._directory = config.output_dir.resolve() / secrets.token_hex(24)
        self._items: dict[str, Artifact] = {}
        self._lock = asyncio.Lock()
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        # Do not create files before an authorized render; validate controlled assets.
        if self._closed:
            raise RuntimeError("Document service closed")
        for key, path in self.config.templates.items():
            if not key.isidentifier() or not path.is_file() or path.suffix.lower() != ".docx":
                raise ValueError("Configured Word template unavailable")

    async def _worker(self, operation: Callable[[], T]) -> T:
        return await wait_for_cleanup(asyncio.create_task(asyncio.to_thread(operation)))

    def _prune(self) -> None:
        now = datetime.now(UTC)
        for key, item in list(self._items.items()):
            if item.expires_at <= now:
                (self._directory / f"{key}.docx").unlink(missing_ok=True)
                del self._items[key]

    async def render(self, request: RenderRequest) -> Artifact:
        if request.template_id not in self.config.templates:
            raise ValueError("Template not allowed")
        if any(len(k) > 80 or len(v) > 16000 for k, v in request.data.items()):
            raise ValueError("Report data exceeds limits")
        async with self._lock:
            if self._closed:
                raise ValueError("Document service closed")

            def write() -> Artifact:
                from docxtpl import DocxTemplate

                self._prune()
                if len(self._items) >= 20:
                    raise ValueError("Report capacity reached")
                key = secrets.token_hex(24)
                self._directory.mkdir(parents=True, exist_ok=True)
                temporary = self._directory / f"{key}.tmp"
                destination = self._directory / f"{key}.docx"
                try:
                    document = DocxTemplate(str(self.config.templates[request.template_id]))
                    if document.get_undeclared_template_variables() - request.data.keys():
                        raise ValueError("Required template data unavailable")
                    document.render(request.data, autoescape=True)
                    document.save(temporary)
                    temporary.replace(destination)
                    now = datetime.now(UTC)
                    item = Artifact(
                        id=key,
                        filename=f"report-{key[:8]}.docx",
                        created_at=now,
                        expires_at=now + timedelta(seconds=self.config.ttl_seconds),
                        size=destination.stat().st_size,
                    )
                    self._items[key] = item
                    return item.model_copy(deep=True)
                finally:
                    temporary.unlink(missing_ok=True)

            return await self._worker(write)

    async def list_artifacts(self) -> list[Artifact]:
        async with self._lock:
            await self._worker(self._prune)
            return [item.model_copy(deep=True) for item in self._items.values()]

    async def read(self, artifact_id: str) -> ArtifactContent:
        async with self._lock:

            def read_file() -> ArtifactContent:
                self._prune()
                item = self._items.get(artifact_id)
                if self._closed or item is None:
                    raise ValueError("Report not found or expired")
                return ArtifactContent(
                    metadata=item.model_copy(deep=True),
                    content=(self._directory / f"{item.id}.docx").read_bytes(),
                )

            return await self._worker(read_file)

    async def close(self) -> None:
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._cleanup())
        await wait_for_cleanup(self._close_task)

    async def _cleanup(self) -> None:
        async with self._lock:
            await self._worker(
                lambda: (
                    shutil.rmtree(self._directory, ignore_errors=False)
                    if self._directory.exists()
                    else None
                )
            )
            self._items.clear()
        logging.getLogger(__name__).debug("Document owner cleaned up")

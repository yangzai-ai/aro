"""Start locally: python -m uvicorn api.app:app --host 127.0.0.1 --port 8000."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path

from anyio import CancelScope
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from api.auth import require_auth
from api.sessions import Session, SessionStore
from api.settings import APISettings
from api.sse import encode_event
from api.traces import router as traces_router
from core.config_loader import AgentConfig
from core.kernel.engine import AgentEngine
from core.kernel.events import ExecutionEvent
from core.kernel.services import ExpertDirectory

logger = logging.getLogger(__name__)


class ChatInput(BaseModel):
    """User text, an opaque session identifier and optional configured expert."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=10000)
    session_id: str | None = Field(default=None, max_length=64)
    target_agent: str | None = Field(default=None, min_length=1, max_length=64)


class ApprovalInput(BaseModel):
    """A decision, bound to both a session and one pending operation."""

    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=64)
    approval_id: str = Field(min_length=1, max_length=64)
    approved: StrictBool


async def stream_turn(request: Request, session: Session, message: str) -> AsyncIterator[str]:
    """Bounded event delivery with cancellation on client disconnect."""
    queue: asyncio.Queue[ExecutionEvent | None] = asyncio.Queue(maxsize=64)
    secrets: tuple[str, ...] = (session.engine.llm_config.api_key,)
    configured_token = request.app.state.settings.token
    if configured_token:
        secrets += (configured_token.get_secret_value(),)

    async def publish(event: ExecutionEvent) -> None:
        await queue.put(event)

    async def produce() -> None:
        try:
            # Do not commit incomplete message transcripts on cancellation/errors.
            state = await asyncio.wait_for(
                session.engine.run(message, deepcopy(session.state), stream=True, on_event=publish),
                timeout=request.app.state.settings.turn_timeout_seconds,
            )
            session.state = state
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Agent turn failed (%s)", type(exc).__name__)
            await publish(
                ExecutionEvent(
                    "error",
                    {
                        "code": "turn_failed",
                        "message": "本轮执行失败，请重试或检查服务端配置。",
                        "terminal": True,
                    },
                )
            )
        else:
            trace = session.engine.trace_hook.get_latest_trace()
            await publish(
                ExecutionEvent(
                    "done", {"session_id": session.id, "trace": trace.to_dict() if trace else None}
                )
            )
        finally:
            # Avoid retaining all past traces in a long-lived browser session.
            session.engine.trace_hook.traces[:] = session.engine.trace_hook.traces[-1:]

    async def run_and_signal() -> None:
        try:
            await produce()
        finally:
            # Cancellation must not block forever trying to append to a full queue.
            current_task = asyncio.current_task()
            if current_task is None or not current_task.cancelling():
                await queue.put(None)

    session.task = asyncio.create_task(run_and_signal())
    try:
        yield encode_event(ExecutionEvent("session", {"session_id": session.id}))
        while not await request.is_disconnected():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            if event is None:
                break
            yield encode_event(event, secrets)
    finally:
        # Starlette may cancel this generator inside an AnyIO cancel scope.
        # Shield cleanup so the pending Future is removed before releasing busy.
        with CancelScope(shield=True):
            session.task.cancel()
            try:
                await asyncio.gather(session.task, return_exceptions=True)
            finally:
                session.busy = False
                session.updated = time.monotonic()


def create_app(
    settings: APISettings | None = None,
    engine_factory: Callable[[], AgentEngine | Awaitable[AgentEngine]] | None = None,
    *,
    expert_directory: Callable[[AgentConfig], ExpertDirectory] | None = None,
) -> FastAPI:
    """Build the app with overridable settings/factory for isolated tests."""
    configured = settings or APISettings()
    store = SessionStore(configured, engine_factory, expert_directory=expert_directory)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        async def cleanup() -> None:
            while True:
                await asyncio.sleep(min(60, configured.session_ttl_seconds))
                await store.expire()

        store.load_experts()
        task = asyncio.create_task(cleanup())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await store.close()

    application = FastAPI(title="ARO API", version="0.5.0", lifespan=lifespan)
    application.state.settings, application.state.sessions = configured, store
    application.include_router(traces_router)
    application.include_router(traces_router, prefix="/api", include_in_schema=False)

    @application.get("/health")
    @application.get("/api/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        """Liveness only; does not expose model or connection configuration."""
        return {"status": "ok"}

    @application.post("/chat", dependencies=[Depends(require_auth)])
    @application.post("/api/chat", dependencies=[Depends(require_auth)], include_in_schema=False)
    async def chat(body: ChatInput, request: Request) -> StreamingResponse:
        await store.expire()
        session = await store.reserve(body.session_id, target_agent=body.target_agent)
        return StreamingResponse(
            stream_turn(request, session, body.message),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "X-Session-Id": session.id,
            },
        )

    @application.post("/approve", status_code=202, dependencies=[Depends(require_auth)])
    @application.post(
        "/api/approve",
        status_code=202,
        dependencies=[Depends(require_auth)],
        include_in_schema=False,
    )
    async def approve(body: ApprovalInput) -> dict[str, str]:
        session = store.sessions.get(body.session_id)
        manager = session.approvals if session else None
        if manager is None:
            raise HTTPException(404, "Approval not found")
        try:
            manager.decide(body.approval_id, body.session_id, body.approved)
        except KeyError as exc:
            raise HTTPException(404, "Approval not found") from exc
        except ValueError as exc:
            raise HTTPException(409, "Approval already decided or expired") from exc
        return {"status": "accepted"}

    distribution = Path(__file__).resolve().parents[1] / "frontend/dist"
    if (distribution / "index.html").is_file() and (distribution / "assets").is_dir():
        application.mount("/assets", StaticFiles(directory=distribution / "assets"), name="assets")

        @application.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(distribution / "index.html")

    return application


app = create_app()

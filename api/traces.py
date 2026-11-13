"""Read retained, completed traces within one live session; no history cache."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel

from api.auth import require_auth
from api.sessions import Session
from api.sse import redact
from core.kernel.trace import AgentTrace


class ToolCallView(BaseModel):
    tool_name: str
    args: dict[str, Any]
    result: Any = None
    success: bool
    error: str | None
    start_time: float
    end_time: float
    duration_ms: float
    call_id: str
    approval_duration_ms: float | None
    execution_duration_ms: float
    hook_errors: list[dict[str, str]]


class TraceView(BaseModel):
    trace_id: str
    thread_id: str | None
    user_input: str
    agent_response: str
    tool_calls: list[ToolCallView]
    start_time: float
    end_time: float | None
    duration_ms: float | None
    error: str | None


router = APIRouter(tags=["traces"], dependencies=[Depends(require_auth)])


async def live_session(request: Request, session_id: str) -> Session:
    store = request.app.state.sessions
    await store.expire()
    session: Session | None = store.sessions.get(session_id)
    if session is None:
        raise HTTPException(404, "Session or trace not found")
    return session


def trace_view(request: Request, session: Session, trace: AgentTrace) -> TraceView:
    secrets: tuple[str, ...] = (session.engine.llm_config.api_key,)
    token = request.app.state.settings.token
    if token:
        secrets += (token.get_secret_value(),)
    return TraceView.model_validate(redact(trace.to_dict(), secrets))


@router.get("/traces/{trace_id}", response_model=TraceView)
async def get_trace(
    request: Request,
    trace_id: str,
    session_id: Annotated[str, Header(alias="X-ARO-Session", min_length=1, max_length=64)],
) -> TraceView:
    """Get a completed trace only in the specified live session (404 otherwise)."""
    session = await live_session(request, session_id)
    for trace in session.engine.trace_hook.get_all_traces():
        if trace.trace_id == trace_id:
            return trace_view(request, session, trace)
    raise HTTPException(404, "Session or trace not found")


@router.get("/sessions/{session_id}/traces", response_model=list[TraceView])
async def list_traces(
    request: Request,
    session_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[TraceView]:
    """List retained completed traces, oldest first; HTTP keeps only the latest turn."""
    session = await live_session(request, session_id)
    return [
        trace_view(request, session, trace)
        for trace in session.engine.trace_hook.get_all_traces()[offset : offset + limit]
    ]

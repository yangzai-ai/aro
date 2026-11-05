"""Serializable agent boundary, independent of transport and supervisor state."""

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from contextvars import copy_context
from datetime import date
from typing import Protocol, runtime_checkable

from langchain_core.messages import SystemMessage
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from core.kernel.engine import AgentEngine
from core.kernel.events import ExecutionEvent, emit_event
from core.kernel.state import AgentState


class AgentContext(BaseModel):
    """Host-provided identity/correlation only; JSON round-trippable for any transport."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    thread_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(default="local", min_length=1, max_length=128)
    user_id: str | None = Field(default=None, min_length=1, max_length=128)
    parent_call_id: str = Field(default="", max_length=256)
    depth: int = Field(default=0, ge=0, le=1)


class AgentResult(BaseModel):
    """Return data only, never an Engine, live message object, or shared state."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    agent_name: str
    answer: str
    success: bool = True
    error: str | None = None
    trace: dict[str, JsonValue] = Field(default_factory=dict)


@runtime_checkable
class AgentHandle(Protocol):
    """Future transports implement this boundary without changing the supervisor.

    task, context and result are JSON-safe; no callback, AgentState or in-process
    objects occur in the interface. v0.7 implements local invocation only.
    """

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    async def invoke(self, task: str, context: AgentContext) -> AgentResult: ...


class LocalAgentHandle:
    """Run an existing Engine in a host-managed resource scope with fresh L1."""

    def __init__(
        self,
        name: str,
        description: str,
        engine_scope: Callable[[], AbstractAsyncContextManager[AgentEngine]],
    ) -> None:
        self.name, self.description, self.engine_scope = name, description, engine_scope

    async def invoke(self, task: str, context: AgentContext) -> AgentResult:
        """One-way invocation; parent messages and state are never exposed."""
        if context.depth != 0:
            raise ValueError("Nested delegation is outside v0.7 scope")
        invocation_id = str(uuid.uuid4())
        parent_context = copy_context()

        async def forward(event: ExecutionEvent) -> None:
            # Specialist prose is not the final supervisor answer. Preserve it as
            # a separate event instead of concatenating two answers in chat.
            kind = event.type
            if event.type == "token":
                kind = "agent_token"
            elif event.type == "reasoning":
                kind = "agent_reasoning"
            data = dict(event.data)
            data.update(agent_name=self.name, parent_call_id=context.parent_call_id)
            if "call_id" in data:
                data["call_id"] = f"{invocation_id}:{data['call_id']}"
            if "tool_name" in data:
                data["tool_name"] = f"{self.name}/{data['tool_name']}"
            # The child's active sink is this forwarder. Publish under the
            # captured parent scope to avoid recursively forwarding to ourselves.
            await asyncio.create_task(emit_event(kind, **data), context=parent_context.copy())

        await emit_event("agent_start", agent_name=self.name, parent_call_id=context.parent_call_id)
        try:
            async with self.engine_scope() as engine:
                # No child writes: a supervisor cannot authorize another agent's
                # write by invoking this read-only delegation tool.
                if any(tool.permission != "read" for tool in engine.tools):
                    raise ValueError("v0.7 local delegation permits read-only specialists")
                child_context: dict[str, object] = {
                    "thread_id": invocation_id,
                    "parent_thread_id": context.thread_id,
                    "tenant_id": context.tenant_id,
                    "delegation_depth": 1,
                }
                if context.user_id is not None:
                    child_context["user_id"] = context.user_id
                state = AgentState(
                    messages=[
                        SystemMessage(
                            content=f"当前日期：{date.today().isoformat()}\n{engine.agent_config.system_prompt}"
                        )
                    ],
                    context=child_context,
                )
                # Child runs inside the outer execution scope but has its own
                # engine event_scope; forward explicitly so nested events survive.
                state = await engine.run(task, state, stream=True, on_event=forward)
                trace = engine.trace_middleware.get_latest_trace()
                result = AgentResult(
                    agent_name=self.name,
                    answer=str(state["messages"][-1].content),
                    trace=trace.to_dict() if trace else {},
                )
            await emit_event(
                "agent_done",
                agent_name=self.name,
                parent_call_id=context.parent_call_id,
                trace=result.trace,
            )
            return result
        except asyncio.CancelledError:
            raise
        except Exception:
            await emit_event(
                "agent_failed", agent_name=self.name, parent_call_id=context.parent_call_id
            )
            raise

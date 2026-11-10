"""Bounded single-process sessions, with an isolated engine/trace per session."""

import asyncio
import inspect
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import date
from typing import Any

from fastapi import HTTPException
from langchain_core.messages import SystemMessage

from api.settings import APISettings
from core.config_loader import AgentConfig, load_llm_config
from core.kernel.engine import AgentEngine
from core.kernel.lifecycle import wait_for_cleanup
from core.kernel.services import (
    ApprovalService,
    ExpertDirectory,
    ExpertInfo,
    LocalExpert,
    hook_service,
)
from core.kernel.state import AgentState


@dataclass
class Session:
    """Conversation data and currently running task owned by one opaque ID."""

    id: str
    engine: AgentEngine
    state: AgentState
    updated: float
    target_agent: str | None = None
    scope: AsyncExitStack | None = None
    close_task: asyncio.Task[None] | None = None
    busy: bool = False
    task: asyncio.Task[None] | None = None

    @property
    def approvals(self) -> ApprovalService | None:
        """Resolve the optional host service without knowing its plugin class."""
        service = hook_service(self.engine.hooks, "approvals")
        if service is not None and not isinstance(service, ApprovalService):
            raise TypeError("Invalid approvals service")
        return service


class SessionStore:
    """Each browser session has its own model client, tools and mutable trace."""

    def __init__(
        self,
        settings: APISettings,
        factory: Callable[[], AgentEngine | Awaitable[AgentEngine]] | None = None,
        *,
        expert_directory: Callable[[AgentConfig], ExpertDirectory] | None = None,
    ) -> None:
        self.settings = settings
        self.factory = factory or self._create_engine
        self.sessions: dict[str, Session] = {}
        self._creating = 0
        self._pending: set[asyncio.Task[Any]] = set()
        self._closing = False
        self.experts: list[ExpertInfo] = []
        self._expert_handles: dict[str, LocalExpert] = {}
        self._expert_directory = expert_directory
        self._experts_loaded = False

    def load_experts(self) -> None:
        """Load a deployment catalogue without starting engines or optional models."""
        if self._experts_loaded:
            return
        if self._expert_directory is not None:
            directory = self._expert_directory(AgentConfig(self.settings.config))
            self.experts = list(directory.catalogue())
            self._expert_handles = {item.name: directory.get(item.name) for item in self.experts}
        self._experts_loaded = True

    async def _create_engine(self) -> AgentEngine:
        config = AgentConfig(self.settings.config)
        engine = await AgentEngine.create(load_llm_config(), config)
        try:
            service = hook_service(engine.hooks, "approvals")
            if service is not None and (
                not isinstance(service, ApprovalService) or service.mode != "api"
            ):
                raise ValueError("HTTP service requires approval mode=api")
        except BaseException:
            await engine.close()
            raise
        return engine

    async def reserve(self, session_id: str | None, *, target_agent: str | None = None) -> Session:
        """Reserve before returning response headers; reject overlapping turns."""
        if self._closing:
            raise HTTPException(503, "Service shutting down")
        if target_agent is not None:
            self.load_experts()
            if target_agent not in self._expert_handles:
                raise HTTPException(400, "Unknown target agent")
        if session_id is not None:
            session = self.sessions.get(session_id)
            if session is None:
                raise HTTPException(404, "Session not found or expired; start a new conversation")
            if session.target_agent != target_agent:
                raise HTTPException(400, "Session belongs to a different agent")
        else:
            if len(self.sessions) + self._creating >= self.settings.max_sessions:
                raise HTTPException(503, "Session capacity reached; retry later")
            self._creating += 1
            task = asyncio.current_task()
            if task is not None:
                self._pending.add(task)
            scope = AsyncExitStack() if target_agent is not None else None
            try:
                if scope is not None and target_agent is not None:
                    engine = await scope.enter_async_context(
                        self._expert_handles[target_agent].engine_scope()
                    )
                    if any(tool.permission != "read" for tool in engine.tools):
                        raise ValueError("Direct local experts permit read-only tools")
                else:
                    result = self.factory()
                    engine = await result if inspect.isawaitable(result) else result
                if self._closing:
                    if scope is not None:
                        await scope.aclose()
                    else:
                        await engine.close()
                    raise RuntimeError("Service shutting down")
            except asyncio.CancelledError:
                if scope is not None:
                    await scope.aclose()
                raise
            except Exception as exc:
                if scope is not None:
                    await scope.aclose()
                # Never return configuration/SDK exceptions containing credentials.
                raise HTTPException(
                    503, "Agent configuration unavailable; check server configuration"
                ) from exc
            finally:
                self._creating -= 1
                if task is not None:
                    self._pending.discard(task)
            try:
                identity = str(uuid.uuid4())
                source: Any = engine.agent_config.capability_config.get("helpdesk_tools", {}).get(
                    "data_source", "mock"
                )
                state = AgentState(
                    messages=[
                        SystemMessage(
                            content=f"当前日期：{date.today().isoformat()}\n数据源：{source}\n{engine.agent_config.system_prompt}"
                        )
                    ],
                    context={"thread_id": identity},
                )
                state["context"]["tenant_id"] = self.settings.tenant_id
                if self.settings.user_id is not None:
                    state["context"]["user_id"] = self.settings.user_id
                session = Session(
                    identity,
                    engine,
                    state,
                    time.monotonic(),
                    target_agent=target_agent,
                    scope=scope,
                )
                self.sessions[identity] = session
            except BaseException:
                if scope is not None:
                    await scope.aclose()
                else:
                    await engine.close()
                raise
        if session.busy:
            raise HTTPException(409, "A turn is already running for this session")
        if len(session.state["messages"]) >= self.settings.max_messages:
            raise HTTPException(409, "Conversation limit reached; start a new conversation")
        session.busy = True
        return session

    async def close_session(self, session: Session) -> None:
        """Cancel outstanding work before closing the session's HTTP client."""

        async def release() -> None:
            if session.task is not None and not session.task.done():
                session.task.cancel()
                await asyncio.gather(session.task, return_exceptions=True)
            try:
                await session.engine.close_session(session.id)
            finally:
                if session.scope is not None:
                    await session.scope.aclose()
                else:
                    await session.engine.close()

        if session.close_task is None:
            session.close_task = asyncio.create_task(release())
        await wait_for_cleanup(session.close_task)

    async def expire(self) -> None:
        """Evict only idle sessions; active turns have their own timeout."""
        now = time.monotonic()
        for key, session in list(self.sessions.items()):
            if not session.busy and now - session.updated >= self.settings.session_ttl_seconds:
                self.sessions.pop(key, None)
                try:
                    await self.close_session(session)
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "Expired session cleanup failed (%s)", type(exc).__name__
                    )

    async def close(self) -> None:
        """Release every session on server shutdown."""
        self._closing = True
        pending = [task for task in self._pending if task is not asyncio.current_task()]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        results = await asyncio.gather(
            *(self.close_session(session) for session in self.sessions.values()),
            return_exceptions=True,
        )
        self.sessions.clear()
        for result in results:
            if isinstance(result, BaseException):
                logging.getLogger(__name__).warning(
                    "Session shutdown cleanup failed (%s)", type(result).__name__
                )

"""Bounded single-process sessions, with an isolated engine/trace per session."""

import asyncio
import inspect
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from fastapi import HTTPException
from langchain_core.messages import SystemMessage

from api.settings import APISettings
from capabilities.hitl.approval_manager import ApprovalManager
from capabilities.hitl.capability import HITLCapability
from core.config_loader import AgentConfig, load_llm_config
from core.kernel.engine import AgentEngine
from core.kernel.state import AgentState


@dataclass
class Session:
    """Conversation data and currently running task owned by one opaque ID."""

    id: str
    engine: AgentEngine
    state: AgentState
    updated: float
    busy: bool = False
    task: asyncio.Task[None] | None = None

    @property
    def approvals(self) -> ApprovalManager | None:
        """Find the optional approval plugin at the allowed API composition layer."""
        for capability in self.engine.capabilities:
            if isinstance(capability, HITLCapability):
                return capability.approval_manager
        return None


class SessionStore:
    """Each browser session has its own model client, tools and mutable trace."""

    def __init__(
        self,
        settings: APISettings,
        factory: Callable[[], AgentEngine | Awaitable[AgentEngine]] | None = None,
    ) -> None:
        self.settings = settings
        self.factory = factory or self._create_engine
        self.sessions: dict[str, Session] = {}
        self._creating = 0
        self._pending: set[asyncio.Task[Any]] = set()
        self._closing = False

    async def _create_engine(self) -> AgentEngine:
        config = AgentConfig(self.settings.config)
        mode = config.capability_config.get("hitl", {}).get("mode")
        if "hitl" in config.capabilities and mode != "api":
            raise ValueError("HTTP service requires hitl mode=api")
        return await AgentEngine.create(load_llm_config(), config)

    async def reserve(self, session_id: str | None) -> Session:
        """Reserve before returning response headers; reject overlapping turns."""
        if self._closing:
            raise HTTPException(503, "Service shutting down")
        if session_id is not None:
            session = self.sessions.get(session_id)
            if session is None:
                raise HTTPException(404, "Session not found or expired; start a new conversation")
        else:
            if len(self.sessions) + self._creating >= self.settings.max_sessions:
                raise HTTPException(503, "Session capacity reached; retry later")
            self._creating += 1
            task = asyncio.current_task()
            if task is not None:
                self._pending.add(task)
            try:
                result = self.factory()
                engine = await result if inspect.isawaitable(result) else result
                if self._closing:
                    await engine.close()
                    raise RuntimeError("Service shutting down")
            except Exception as exc:
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
                session = Session(identity, engine, state, time.monotonic())
                self.sessions[identity] = session
            except BaseException:
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
        if session.task is not None and not session.task.done():
            session.task.cancel()
            await asyncio.gather(session.task, return_exceptions=True)
        await session.engine.close()

    async def expire(self) -> None:
        """Evict only idle sessions; active turns have their own timeout."""
        now = time.monotonic()
        for key, session in list(self.sessions.items()):
            if not session.busy and now - session.updated >= self.settings.session_ttl_seconds:
                self.sessions.pop(key, None)
                await self.close_session(session)

    async def close(self) -> None:
        """Release every session on server shutdown."""
        self._closing = True
        pending = [task for task in self._pending if task is not asyncio.current_task()]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.gather(*(self.close_session(session) for session in self.sessions.values()))
        self.sessions.clear()

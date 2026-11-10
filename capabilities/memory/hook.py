"""Automatic final-response fact writing; storage failure never fails a turn."""

import asyncio
import logging
from contextvars import ContextVar

from langchain_core.messages import AIMessage, HumanMessage

from capabilities.memory.extractor import extract_facts
from capabilities.memory.identity import Namespace, memory_namespace
from capabilities.memory.store import MemoryStore
from core.kernel.hook import Hook
from core.kernel.state import AgentState

logger = logging.getLogger(__name__)


class MemoryWriteHook(Hook):
    """Bind trusted identity before calls; write facts once at the final response."""

    def __init__(
        self, store: MemoryStore, scope: ContextVar[Namespace | None], timeout: float
    ) -> None:
        self.store, self.scope, self.timeout = store, scope, timeout

    async def before(self, state: AgentState) -> AgentState:
        self.scope.set(None)
        try:
            self.scope.set(memory_namespace(state))
        except ValueError:
            logger.warning("Memory disabled for turn: invalid runtime identity")
        return state

    async def after(self, state: AgentState) -> AgentState:
        try:
            messages = state["messages"]
            last = messages[-1] if messages else None
            namespace = self.scope.get()
            if namespace is None or not isinstance(last, AIMessage) or last.tool_calls:
                return state
            human = next(
                (message for message in reversed(messages) if isinstance(message, HumanMessage)),
                None,
            )
            if human is None or not isinstance(human.content, str):
                return state
            text = human.content

            async def save() -> None:
                for fact in extract_facts(text):
                    await self.store.save(namespace, fact)

            # Bound total extra latency; no detached tasks can outlive the session.
            await asyncio.wait_for(save(), self.timeout)
        except Exception as exc:
            logger.warning("Memory write skipped (%s)", type(exc).__name__)
        finally:
            self.scope.set(None)
        return state

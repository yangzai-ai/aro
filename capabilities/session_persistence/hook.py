"""Lifecycle-driven snapshots, using LangChain's complete message serialization."""

from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ToolMessage,
    messages_from_dict,
    messages_to_dict,
)

from core.kernel.context import get_thread_id
from core.kernel.hook import Hook
from core.kernel.state import AgentState

from .backends.base import Backend

UNKNOWN_TOOL_RESULT = (
    "因服务中断，此工具调用未记录结果，执行状态未知；未自动重放，请先查询业务系统核实。"
)


class SessionPersistenceHook(Hook):
    def __init__(self, backend: Backend, *, delete_on_session_close: bool = False) -> None:
        self.backend = backend
        self.delete_on_session_close = delete_on_session_close

    async def on_start(self, config: dict[str, Any]) -> None:
        await self.backend.initialize()

    async def on_session_load(self, session_id: str) -> AgentState | None:
        data = await self.backend.load(session_id)
        if data is None:
            return None
        if data.get("version") != 1 or not isinstance(data.get("messages"), list):
            raise ValueError("Unsupported or malformed session snapshot")
        context = data.get("context", {})
        if not isinstance(context, dict):
            raise ValueError("Malformed session context")
        messages = self._deserialize_messages(data["messages"])
        restored = AgentState(messages=self._fill_dangling_calls(messages), context=context)
        if "tenant_id" in data:
            restored["tenant_id"] = data["tenant_id"]
        # Engine.load_session owns identity checks; never coerce/rewrite identity.
        return restored

    async def before(self, state: AgentState) -> AgentState:
        return state

    async def after(self, state: AgentState) -> AgentState:
        session_id = get_thread_id()
        if session_id is None:
            raise RuntimeError("Session persistence requires an Engine thread_id scope")
        data: dict[str, Any] = {
            "version": 1,
            "messages": self._serialize_messages(state["messages"]),
            "context": state.get("context", {}),
        }
        if "tenant_id" in state:
            data["tenant_id"] = state["tenant_id"]
        await self.backend.save(session_id, data)
        return state

    async def on_session_close(self, session_id: str) -> None:
        if self.delete_on_session_close:
            await self.backend.delete(session_id)

    async def on_stop(self) -> None:
        await self.backend.close()

    @staticmethod
    def _serialize_messages(messages: list[BaseMessage]) -> list[dict[str, Any]]:
        return messages_to_dict(messages)

    @staticmethod
    def _deserialize_messages(data: list[dict[str, Any]]) -> list[BaseMessage]:
        return messages_from_dict(data)

    @staticmethod
    def _fill_dangling_calls(messages: list[BaseMessage]) -> list[BaseMessage]:
        """Complete each batch before subsequent prose/input, without replaying it.

        A missing result is evidence of unknown outcome, not of non-execution.
        Ambiguous IDs/results fail closed rather than inventing an association.
        """
        result: list[BaseMessage] = []
        pending: dict[str, dict[str, Any]] = {}
        batch: dict[str, dict[str, Any]] = {}

        def finish_batch() -> None:
            for call_id, call in pending.items():
                result.append(
                    ToolMessage(
                        content=UNKNOWN_TOOL_RESULT,
                        tool_call_id=call_id,
                        name=call["name"],
                        status="error",
                    )
                )
            pending.clear()
            batch.clear()

        for message in messages:
            if isinstance(message, ToolMessage):
                matched = pending.pop(message.tool_call_id, None)
                if matched is None or (
                    message.name is not None and message.name != matched["name"]
                ):
                    raise ValueError("Ambiguous tool result in session snapshot")
                result.append(message)
                continue
            finish_batch()
            if isinstance(message, AIMessage):
                for call in message.tool_calls:
                    call_id, name = call.get("id"), call.get("name")
                    if not isinstance(call_id, str) or not call_id or call_id in batch:
                        raise ValueError("Ambiguous tool call ID in session snapshot")
                    if not isinstance(name, str) or not name:
                        raise ValueError("Missing tool name in session snapshot")
                    batch[call_id] = dict(call)
                    pending[call_id] = dict(call)
            result.append(message)
        finish_batch()
        return result

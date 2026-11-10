"""Tool-level HITL hook."""

from typing import Any

from core.kernel.hook import Hook
from core.kernel.state import AgentState
from core.tools.base import BaseTool, ToolResult

from .approval_manager import ApprovalManager


class HITLHook(Hook):
    """Pause write tools until a human approves their exact arguments."""

    _handles_write = True

    def __init__(
        self, approval_manager: ApprovalManager, tool_map: dict[str, BaseTool] | None = None
    ) -> None:
        self.approval_manager = approval_manager
        self.tool_map = tool_map or {}

    def bind_tools(self, tools: dict[str, BaseTool]) -> None:
        """Use the filtered registry supplied by the kernel's generic contract."""
        self.tool_map = dict(tools)

    async def before(self, state: AgentState) -> AgentState:
        return state

    async def after(self, state: AgentState) -> AgentState:
        return state

    async def before_tool(
        self, tool_name: str, tool_args: dict[str, Any], state: AgentState
    ) -> tuple[bool, str | None]:
        tool = self.tool_map.get(tool_name)
        if tool is None or tool.permission != "write":
            return True, None
        thread_id = state.get("context", {}).get("thread_id", "unknown")
        approved = await self.approval_manager.request_approval(
            tool_name, tool_args, str(thread_id)
        )
        return (True, None) if approved else (False, "User rejected the operation")

    async def after_tool(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_result: ToolResult,
        state: AgentState,
    ) -> AgentState:
        return state

"""Hook contract.

Hook是第二个扩展点：在 Agent 执行前后插入逻辑。
用于实现横切关注点（记忆、HITL、审计等）。

Hook与工具的区别：
- 工具：LLM 按需调用（主动）
- Hook：框架每轮强制执行（被动）

示例：
- HITL Hook：before 钩子拦截 write 工具，等待人工确认
- 记忆写入Hook：after 钩子自动抽取重要信息存储
- 审计Hook：after 钩子记录所有工具调用
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from core.kernel.state import AgentState
from core.tools.base import BaseTool, ToolResult


class Hook(ABC):
    """Hook基类（契约）.

    Hook在 Agent 循环的前后执行，用于实现：
    - 记忆读取/写入（before 读上下文，after 存新记忆）
    - HITL 审批（before 拦截 write 工具）
    - 审计日志（after 记录执行轨迹）
    - Trace 记录（after 记录工具调用）

    执行顺序：
    1. for mw in hooks: mw.before(state)
    2. LLM 调用 + 工具执行
    3. for mw in reversed(hooks): mw.after(state)
    """

    _handles_write: bool = False
    provides: Mapping[str, object] = {}

    async def on_start(self, config: dict[str, Any]) -> None:  # noqa: B027
        """Initialize once per engine before its first user turn."""

    async def on_session_load(self, session_id: str) -> AgentState | None:
        """Optionally restore a session; the first non-None result wins."""
        return None

    async def on_session_close(self, session_id: str) -> None:  # noqa: B027
        """Release session-owned data when the host closes that session."""

    async def on_stop(self) -> None:  # noqa: B027
        """Release resources owned by on_start, including partial startup."""

    def bind_tools(self, tools: dict[str, BaseTool]) -> None:  # noqa: B027
        """Optionally receive the executable tool registry during assembly."""

    @abstractmethod
    async def before(self, state: AgentState) -> AgentState:
        """在 LLM 调用前执行.

        Args:
            state: 当前状态

        Returns:
            AgentState: 修改后的状态（可以添加上下文、修改消息等）

        Example:
            # 记忆Hook：注入历史上下文
            memory_context = await self.memory_store.search(...)
            state["messages"].insert(0, SystemMessage(content=memory_context))
            return state
        """
        ...

    @abstractmethod
    async def after(self, state: AgentState) -> AgentState:
        """在工具执行后执行.

        Args:
            state: 当前状态（包含 LLM 响应和工具执行结果）

        Returns:
            AgentState: 修改后的状态

        Example:
            # 记忆Hook：抽取重要信息存储
            important_info = await self.extract_memory(state["messages"])
            await self.memory_store.save(important_info)
            return state
        """
        ...

    async def before_tool(
        self, tool_name: str, tool_args: dict[str, Any], state: AgentState
    ) -> tuple[bool, str | None]:
        """Optionally allow or block one tool invocation before execution."""
        return True, None

    async def after_tool(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_result: ToolResult,
        state: AgentState,
    ) -> AgentState:
        """Optionally observe or update state after one tool invocation."""
        return state

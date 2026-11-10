"""Basic tools capability - the first capability implementation.

这是第一个 Capability 实现，用于验证插件机制。
提供一个简单的 mock 工具：get_current_time
"""

from datetime import datetime
from typing import Any

from core.kernel.capability import Capability
from core.kernel.hook import Hook
from core.tools.base import BaseTool, ToolResult


class GetCurrentTimeTool(BaseTool):
    """获取当前时间工具（mock 工具，用于验证）."""

    @property
    def name(self) -> str:
        return "get_current_time"

    @property
    def description(self) -> str:
        return "Get the current date and time"

    @property
    def usage_prompt(self) -> str:
        return """Use this tool when the user asks about the current time or date.

When to use:
- User asks "What time is it?"
- User asks "What's the date today?"
- User needs current timestamp for any reason

When NOT to use:
- User asks about time in a specific timezone (this returns local time only)
- User asks about historical time
- User wants to set or change time

Returns:
- ISO 8601 formatted datetime string (e.g., "2024-01-15T14:30:00")
"""

    @property
    def permission(self) -> str:  # type: ignore
        return "read"

    @property
    def schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}  # 无参数，但必须符合 JSON Schema 格式

    async def run(self, **kwargs: Any) -> ToolResult:
        """执行：返回当前时间."""
        current_time = datetime.now().isoformat()
        return ToolResult(
            success=True,
            output=current_time,
        )


class BasicToolsCapability(Capability):
    """基础工具能力.

    这是第一个 Capability 实现，用于 v0.1 验证插件机制。
    提供一个简单的 mock 工具。
    """

    @property
    def name(self) -> str:
        return "basic_tools"

    def tools(self) -> list[BaseTool]:
        """提供工具列表."""
        return [GetCurrentTimeTool()]

    def hooks(self) -> list[Hook]:
        """不提供Hook."""
        return []


def get_capability(config: dict[str, Any]) -> Capability:
    """工厂函数：创建 BasicToolsCapability 实例.

    Args:
        config: 配置字典（basic_tools 不需要配置）

    Returns:
        Capability: BasicToolsCapability 实例
    """
    return BasicToolsCapability()

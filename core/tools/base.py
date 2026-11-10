"""Tool system base contracts.

工具是第一个扩展点：能力通过提供工具来扩展 Agent 的能力。
BaseTool 定义了工具的统一契约。

关键设计（从 Claude Code 学习）：
- usage_prompt: 详细使用说明（何时用、何时不用、注意事项）
- permission: 权限分级（read/write/forbidden）
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

PermissionLevel = Literal["read", "write", "forbidden"]


@dataclass
class ToolResult:
    """工具执行结果.

    Attributes:
        success: 是否成功
        output: 输出内容（成功时）
        error: 错误信息（失败时）
    """

    success: bool
    output: Any = None
    error: str | None = None


class BaseTool(ABC):
    """工具基类（契约）.

    所有工具必须实现此接口。工具是 Agent 能力的扩展点之一。

    关键字段说明：
    - name: 工具名（snake_case，动词开头，如 query_tickets）
    - description: 一句话描述（给 LLM 看的工具列表）
    - usage_prompt: ★ 详细使用说明（何时用、何时不用、参数注意事项、常见错误）
    - permission: 权限分级
        - "read": 只读操作，直接执行
        - "write": 写操作，默认走 HITL 审批（v0.4）
        - "forbidden": 绝对禁止，不进 LLM 可调用列表
    - schema: 参数 schema（Pydantic model 或 dict）
    """

    # Kernel deadline covers run() only, never hook/approval waiting.
    timeout_seconds: float | None = None

    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """简短描述（一句话）."""
        ...

    @property
    @abstractmethod
    def usage_prompt(self) -> str:
        """详细使用说明.

        这是从 Claude Code 学来的关键设计。
        告诉 LLM：
        - 什么场景下应该用这个工具
        - 什么场景下不应该用
        - 参数的注意事项
        - 常见错误和避免方法

        Example:
            \"\"\"
            Use this tool to query open IT support tickets.

            When to use:
            - User asks about open tickets
            - Need to filter tickets by status

            When NOT to use:
            - User asks about historical data (use get_ticket_history instead)
            - Need to update a ticket (use update_ticket instead)

            Parameters:
            - status: Filter by status. Use "open" for unassigned tickets.
            - assignee: Optional. Filter by assigned person or team.
            \"\"\"
        """
        ...

    @property
    @abstractmethod
    def permission(self) -> PermissionLevel:
        """权限级别."""
        ...

    @property
    @abstractmethod
    def schema(self) -> type[BaseModel] | dict[str, Any]:
        """参数 schema（Pydantic model 或 dict）."""
        ...

    @abstractmethod
    async def run(self, **kwargs: Any) -> ToolResult:
        """执行工具.

        Args:
            **kwargs: 工具参数（根据 schema 验证）

        Returns:
            ToolResult: 执行结果
        """
        ...


# 装饰器形式的工具定义（简化版，供 v0.1 使用）
def tool(
    name: str,
    description: str,
    usage_prompt: str,
    permission: PermissionLevel = "read",
    schema: type[BaseModel] | dict[str, Any] | None = None,
) -> Any:
    """工具装饰器（简化定义）.

    Usage:
        @tool(
            name="get_current_time",
            description="Get current time",
            usage_prompt="Use when user asks 'what time is it'",
            permission="read",
        )
        async def get_time() -> ToolResult:
            return ToolResult(success=True, output=datetime.now().isoformat())

    Note:
        这是简化版装饰器，用于 v0.1 快速定义工具。
        完整版（支持自动 schema 推断）在 v0.2 实现。
    """

    def decorator(func: Any) -> BaseTool:
        class DecoratedTool(BaseTool):
            @property
            def name(self) -> str:
                return name

            @property
            def description(self) -> str:
                return description

            @property
            def usage_prompt(self) -> str:
                return usage_prompt

            @property
            def permission(self) -> PermissionLevel:
                return permission

            @property
            def schema(self) -> type[BaseModel] | dict[str, Any]:
                return schema or {}

            async def run(self, **kwargs: Any) -> ToolResult:
                result = await func(**kwargs)
                if not isinstance(result, ToolResult):
                    raise TypeError("Decorated tools must return ToolResult")
                return result

        return DecoratedTool()

    return decorator

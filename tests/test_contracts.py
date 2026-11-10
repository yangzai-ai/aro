"""Test suite for core contracts (v0.1).

验证三个核心契约接口：
- BaseTool
- Hook
- Capability
"""

import pytest
from langchain_core.messages import HumanMessage

from core.kernel.capability import Capability
from core.kernel.hook import Hook
from core.kernel.state import AgentState
from core.tools.base import BaseTool, ToolResult


class MockTool(BaseTool):
    """Mock tool for testing."""

    @property
    def name(self) -> str:
        return "mock_tool"

    @property
    def description(self) -> str:
        return "A mock tool"

    @property
    def usage_prompt(self) -> str:
        return "Use this for testing"

    @property
    def permission(self) -> str:  # type: ignore
        return "read"

    @property
    def schema(self) -> dict:
        return {}

    async def run(self, **kwargs: any) -> ToolResult:
        return ToolResult(success=True, output="mock_result")


class MockHook(Hook):
    """Mock hook for testing."""

    def __init__(self) -> None:
        self.before_called = False
        self.after_called = False

    async def before(self, state: AgentState) -> AgentState:
        self.before_called = True
        return state

    async def after(self, state: AgentState) -> AgentState:
        self.after_called = True
        return state


class MockCapability(Capability):
    """Mock capability for testing."""

    @property
    def name(self) -> str:
        return "mock_capability"

    def tools(self) -> list[BaseTool]:
        return [MockTool()]

    def hooks(self) -> list[Hook]:
        return [MockHook()]


class TestBaseTool:
    """测试 BaseTool 契约."""

    @pytest.mark.asyncio
    async def test_tool_execution(self) -> None:
        """测试工具执行."""
        tool = MockTool()
        result = await tool.run()

        assert result.success is True
        assert result.output == "mock_result"

    def test_tool_properties(self) -> None:
        """测试工具属性."""
        tool = MockTool()

        assert tool.name == "mock_tool"
        assert tool.description == "A mock tool"
        assert tool.usage_prompt == "Use this for testing"
        assert tool.permission == "read"
        assert tool.schema == {}


class TestHook:
    """测试 Hook 契约."""

    @pytest.mark.asyncio
    async def test_hook_hooks(self) -> None:
        """测试Hook钩子."""
        hook = MockHook()
        state = AgentState(messages=[HumanMessage(content="test")])

        # Before hook
        state = await hook.before(state)
        assert hook.before_called is True

        # After hook
        state = await hook.after(state)
        assert hook.after_called is True


class TestCapability:
    """测试 Capability 契约."""

    def test_capability_tools(self) -> None:
        """测试能力提供的工具."""
        capability = MockCapability()
        tools = capability.tools()

        assert len(tools) == 1
        assert tools[0].name == "mock_tool"

    def test_capability_hooks(self) -> None:
        """测试能力提供的Hook."""
        capability = MockCapability()
        hooks = capability.hooks()

        assert len(hooks) == 1
        assert isinstance(hooks[0], MockHook)

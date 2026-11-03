"""Test suite for capabilities (v0.1).

测试 Capability 的加载和组装逻辑。
"""

import pytest

from capabilities.basic_tools.capability import BasicToolsCapability, get_capability


class TestBasicToolsCapability:
    """测试 BasicToolsCapability."""

    def test_get_capability_factory(self) -> None:
        """测试工厂函数."""
        capability = get_capability({})

        assert isinstance(capability, BasicToolsCapability)
        assert capability.name == "basic_tools"

    def test_provides_tools(self) -> None:
        """测试提供工具."""
        capability = BasicToolsCapability()
        tools = capability.tools()

        assert len(tools) == 1
        assert tools[0].name == "get_current_time"
        assert tools[0].permission == "read"

    def test_no_middlewares(self) -> None:
        """测试不提供中间件."""
        capability = BasicToolsCapability()
        middlewares = capability.middlewares()

        assert len(middlewares) == 0

    @pytest.mark.asyncio
    async def test_tool_execution(self) -> None:
        """测试工具执行."""
        capability = BasicToolsCapability()
        tools = capability.tools()
        tool = tools[0]

        result = await tool.run()

        assert result.success is True
        assert result.output is not None
        # 验证返回的是 ISO 8601 格式时间
        assert "T" in result.output  # ISO format contains 'T'

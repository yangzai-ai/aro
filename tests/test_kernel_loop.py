"""Test suite for kernel loop (v0.1).

测试内核循环的核心逻辑：
- 裸 Agent（无工具、无Hook）
- 工具调用
- Hook执行顺序
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from core.kernel.hook import Hook
from core.kernel.loop import KernelLoop
from core.kernel.state import AgentState
from core.llm.provider import LLMProvider, LLMResponse
from core.tools.base import BaseTool, ToolResult


class FakeLLMProvider(LLMProvider):
    """Fake LLM for testing (no real API calls)."""

    def __init__(self, response: str, tool_calls: list[dict] | None = None) -> None:
        self.response = response
        self.tool_calls = tool_calls

    async def chat(self, messages: list, tools: list | None = None, **kwargs: any) -> LLMResponse:
        return LLMResponse(
            content=self.response,
            tool_calls=self.tool_calls,
        )

    async def chat_stream(self, messages: list, tools: list | None = None, **kwargs: any) -> any:
        raise NotImplementedError()


class FakeTool(BaseTool):
    """Fake tool for testing."""

    @property
    def name(self) -> str:
        return "fake_tool"

    @property
    def description(self) -> str:
        return "A fake tool"

    @property
    def usage_prompt(self) -> str:
        return "Use for testing"

    @property
    def permission(self) -> str:  # type: ignore
        return "read"

    @property
    def schema(self) -> dict:
        return {}

    async def run(self, **kwargs: any) -> ToolResult:
        return ToolResult(success=True, output="fake_result")


class SpyHook(Hook):
    """Hook that records execution order."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.execution_log: list[str] = []

    async def before(self, state: AgentState) -> AgentState:
        self.execution_log.append(f"{self.name}_before")
        return state

    async def after(self, state: AgentState) -> AgentState:
        self.execution_log.append(f"{self.name}_after")
        return state


class TestKernelLoop:
    """测试内核循环."""

    @pytest.mark.asyncio
    async def test_bare_agent(self) -> None:
        """测试裸 Agent（无工具、无Hook）."""
        llm = FakeLLMProvider(response="Hello!")
        loop = KernelLoop(llm=llm, tools=[], hooks=[])

        state = AgentState(messages=[HumanMessage(content="Hi")])
        result = await loop.run_once(state)

        # 验证：添加了 AI 响应
        assert len(result["messages"]) == 2
        assert isinstance(result["messages"][1], AIMessage)
        assert result["messages"][1].content == "Hello!"

    @pytest.mark.asyncio
    async def test_tool_execution(self) -> None:
        """测试工具调用."""
        # LLM 返回 tool_calls
        tool_calls = [
            {
                "id": "call_1",
                "name": "fake_tool",
                "args": {},
            }
        ]
        llm = FakeLLMProvider(response="I'll use the tool", tool_calls=tool_calls)

        tool = FakeTool()
        loop = KernelLoop(llm=llm, tools=[tool], hooks=[])

        state = AgentState(messages=[HumanMessage(content="Use the tool")])
        result = await loop.run_once(state)

        # 验证：AI 消息 + 工具消息
        assert len(result["messages"]) == 3
        assert isinstance(result["messages"][1], AIMessage)
        assert result["messages"][2].content == "fake_result"

    @pytest.mark.asyncio
    async def test_hook_execution_order(self) -> None:
        """测试Hook执行顺序（before 正序，after 逆序）."""
        llm = FakeLLMProvider(response="OK")

        # 使用共享的日志列表来记录执行顺序
        execution_log: list[str] = []

        class OrderSpyHook(Hook):
            """Hook that records execution order to shared log."""

            def __init__(self, name: str, log: list[str]) -> None:
                self.name = name
                self.log = log

            async def before(self, state: AgentState) -> AgentState:
                self.log.append(f"{self.name}_before")
                return state

            async def after(self, state: AgentState) -> AgentState:
                self.log.append(f"{self.name}_after")
                return state

        mw1 = OrderSpyHook("mw1", execution_log)
        mw2 = OrderSpyHook("mw2", execution_log)

        loop = KernelLoop(llm=llm, tools=[], hooks=[mw1, mw2])

        state = AgentState(messages=[HumanMessage(content="Test")])
        await loop.run_once(state)

        # 验证执行顺序：mw1_before -> mw2_before -> mw2_after -> mw1_after
        assert execution_log == [
            "mw1_before",
            "mw2_before",
            "mw2_after",
            "mw1_after",
        ]

    @pytest.mark.asyncio
    async def test_forbidden_tools_not_available(self) -> None:
        """测试 forbidden 工具不进 LLM 可调用列表."""
        llm = FakeLLMProvider(response="OK")

        class ForbiddenTool(FakeTool):
            @property
            def name(self) -> str:
                return "forbidden_tool"

            @property
            def permission(self) -> str:  # type: ignore
                return "forbidden"

        read_tool = FakeTool()
        forbidden_tool = ForbiddenTool()

        loop = KernelLoop(
            llm=llm,
            tools=[read_tool, forbidden_tool],
            hooks=[],
        )

        # 验证：只有 read_tool 在可用列表中
        assert len(loop._available_tools) == 1
        assert loop._available_tools[0].name == "fake_tool"

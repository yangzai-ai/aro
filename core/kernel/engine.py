"""Engine - capability assembler and executor.

执行引擎：装配 Capabilities，收集 tools + middlewares，交给内核循环。
这是"插件化"机制的实现：
- 读取配置中的 capabilities 列表
- 动态加载对应的 Capability 实例
- 收集它们贡献的 tools 和 middlewares
- 传给内核循环

内核循环本身不知道 Capability 的存在，只知道 tools + middlewares。
"""

import asyncio
import logging
import uuid
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver

from core.config_loader import AgentConfig, LLMConfig
from core.kernel.capability import Capability
from core.kernel.checkpoint import default_checkpointer
from core.kernel.events import EventSink, event_scope
from core.kernel.lifecycle import wait_for_cleanup
from core.kernel.loop import KernelLoop, KernelRun
from core.kernel.middleware import Middleware
from core.kernel.plugin_loader import PluginLoader
from core.kernel.state import AgentState, runtime_context
from core.kernel.trace import TraceMiddleware
from core.llm.openai_provider import OpenAICompatibleProvider
from core.tools.base import BaseTool


class AgentEngine:
    """Agent 执行引擎.

    职责：
    1. 根据配置动态加载 Capabilities
    2. 收集 Capabilities 贡献的 tools 和 middlewares
    3. 初始化 LLM Provider
    4. 创建内核循环
    5. 提供统一的执行接口

    这是"能力插件化"的关键：
    - 配置中列出的能力才加载
    - 未列出的能力，代码完全不 import
    - 内核对具体能力零认知
    """

    def __init__(
        self,
        llm_config: LLMConfig,
        agent_config: AgentConfig,
        *,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
    ):
        """初始化执行引擎.

        Args:
            llm_config: LLM 配置（从环境变量）
            agent_config: Agent 配置（从 YAML）
        """
        self._configure(llm_config, agent_config, checkpointer)
        self.capabilities = self._load_capabilities()
        if any(type(item).start is not Capability.start for item in self.capabilities):
            raise RuntimeError("Async capabilities require await AgentEngine.create(...)")
        self.llm = self._init_llm()
        self._assemble()

    def _configure(
        self,
        llm_config: LLMConfig,
        agent_config: AgentConfig,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
    ) -> None:
        self.llm_config = llm_config
        self.agent_config = agent_config
        self.capabilities = []
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        self.checkpointer = default_checkpointer() if checkpointer is None else checkpointer
        self._owns_checkpointer = checkpointer is None
        self._checkpoint_threads: set[str] = set()

    def _assemble(self) -> None:
        self.tools = self._collect_tools()
        self.middlewares = self._collect_middlewares()
        self.trace_middleware = TraceMiddleware()
        self.middlewares.insert(0, self.trace_middleware)
        self.loop = KernelLoop(llm=self.llm, tools=self.tools, middlewares=self.middlewares)

    @classmethod
    async def create(
        cls,
        llm_config: LLMConfig,
        agent_config: AgentConfig,
        *,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
    ) -> "AgentEngine":
        """Start capabilities before collecting tools; roll back on failure/cancellation."""
        engine = cls.__new__(cls)
        engine._configure(llm_config, agent_config, checkpointer)
        try:
            engine.capabilities = engine._load_capabilities()
            engine.llm = engine._init_llm()
            for capability in engine.capabilities:
                await capability.start()
            engine._assemble()
        except BaseException:
            try:
                await engine.close()
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Startup rollback failed (%s)", type(exc).__name__
                )
            raise
        return engine

    async def close(self) -> None:
        """Release owned resources in reverse order, including on cancelled startup."""
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close_resources())
        await wait_for_cleanup(self._close_task)

    async def _close_resources(self) -> None:
        interrupted = False
        for capability in reversed(self.capabilities):
            try:
                await capability.close()
            except asyncio.CancelledError:
                interrupted = True
                logging.getLogger(__name__).warning("Capability cleanup failed (CancelledError)")
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Capability cleanup failed (%s)", type(exc).__name__
                )
        if hasattr(self, "llm"):
            try:
                await self.llm._client.close()
            except asyncio.CancelledError:
                interrupted = True
                logging.getLogger(__name__).warning("LLM cleanup failed (CancelledError)")
        if self._owns_checkpointer:
            for thread_id in self._checkpoint_threads:
                await self.checkpointer.adelete_thread(thread_id)
            self._checkpoint_threads.clear()
        if interrupted:
            raise RuntimeError("Cleanup was interrupted; remaining cleanup was attempted")

    def _init_llm(self) -> OpenAICompatibleProvider:
        """初始化 LLM Provider.

        Returns:
            OpenAICompatibleProvider: LLM Provider 实例
        """
        # agent.yml 中的 llm 配置可以覆盖环境变量
        model = self.agent_config.llm_config.get("model", self.llm_config.model)
        temperature = self.agent_config.llm_config.get("temperature", self.llm_config.temperature)

        return OpenAICompatibleProvider(
            api_key=self.llm_config.api_key,
            base_url=self.llm_config.api_base,
            model=model,
            temperature=temperature,
        )

    def _load_capabilities(self) -> list[Capability]:
        """动态加载 Capabilities.

        根据配置中的 capabilities 列表，动态 import 对应模块。

        Example:
            capabilities: [basic_tools, knowledge]

            将加载：
            - capabilities.basic_tools.capability.BasicToolsCapability
            - capabilities.knowledge.capability.KnowledgeCapability

        Returns:
            list[Capability]: Capability 实例列表
        """
        for name in self.agent_config.capabilities:
            self.capabilities.extend(
                PluginLoader().load([name], self.agent_config.capability_config)
            )
        return self.capabilities

    def _collect_tools(self) -> list[BaseTool]:
        """收集所有 Capabilities 贡献的工具.

        Returns:
            list[BaseTool]: 工具列表
        """
        tools: list[BaseTool] = []
        for capability in self.capabilities:
            tools.extend(capability.tools())
        tools = [tool for tool in tools if tool.permission != "forbidden"]
        if not self.agent_config.tools:
            return tools
        enabled = set(self.agent_config.tools)
        return [tool for tool in tools if tool.name in enabled]

    def _collect_middlewares(self) -> list[Middleware]:
        """收集所有 Capabilities 贡献的中间件.

        Returns:
            list[Middleware]: 中间件列表
        """
        middlewares: list[Middleware] = []
        for capability in self.capabilities:
            middlewares.extend(capability.middlewares())
        return middlewares

    async def run(
        self,
        user_input: str,
        state: AgentState | None = None,
        *,
        max_tool_rounds: int = 8,
        stream: bool = False,
        on_event: EventSink | None = None,
    ) -> AgentState:
        """执行一轮对话.

        Args:
            user_input: 用户输入
            state: 现有状态（可选，用于多轮对话）

        Returns:
            AgentState: 更新后的状态
        """
        if self._closed:
            raise RuntimeError("Engine is closed")
        if max_tool_rounds < 0:
            raise ValueError("max_tool_rounds must be nonnegative")
        if self.trace_middleware.current_trace is not None:
            raise RuntimeError("AgentEngine supports one active conversation turn at a time")
        # 初始化状态（如果是第一轮）
        if state is None:
            state = AgentState(messages=[])
            # 添加系统提示词（如果有）
            if self.agent_config.system_prompt:
                state["messages"].append(SystemMessage(content=self.agent_config.system_prompt))

        # 添加用户消息
        state["messages"].append(HumanMessage(content=user_input))
        runtime_context(state, create=True).setdefault("thread_id", str(uuid.uuid4()))
        self._checkpoint_threads.add(runtime_context(state)["thread_id"])
        self.trace_middleware.start(state)
        frame = KernelRun(state)
        try:
            with event_scope(on_event):
                state = await self.loop._run(
                    frame,
                    max_tool_rounds=max_tool_rounds,
                    stream=stream,
                    checkpointer=self.checkpointer,
                )
                return state
        except BaseException as exc:
            self.trace_middleware.finish(frame.state, error=type(exc).__name__)
            raise
        finally:
            self.trace_middleware.finish(frame.state)

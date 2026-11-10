"""Engine - capability assembler and executor.

执行引擎：装配 Capabilities，收集 tools + hooks，交给内核循环。
这是"插件化"机制的实现：
- 读取配置中的 capabilities 列表
- 动态加载对应的 Capability 实例
- 收集它们贡献的 tools 和 hooks
- 传给内核循环

内核循环本身不知道 Capability 的存在，只知道 tools + hooks。
"""

import asyncio
import logging
import uuid
from copy import deepcopy
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver

from core.config_loader import AgentConfig, LLMConfig
from core.kernel.capability import Capability
from core.kernel.checkpoint import default_checkpointer
from core.kernel.discovery import discover_capabilities
from core.kernel.events import EventSink, event_scope
from core.kernel.hook import Hook
from core.kernel.lifecycle import wait_for_cleanup
from core.kernel.loop import KernelLoop, KernelRun
from core.kernel.plugin_loader import PluginLoader
from core.kernel.state import AgentState, runtime_context
from core.kernel.trace import TraceHook
from core.llm.openai_provider import OpenAICompatibleProvider
from core.tools.base import BaseTool


class AgentEngine:
    """Agent 执行引擎.

    职责：
    1. 根据配置动态加载 Capabilities
    2. 收集 Capabilities 贡献的 tools 和 hooks
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
        capabilities: list[Capability] | None = None,
    ):
        """初始化执行引擎.

        Args:
            llm_config: LLM 配置（从环境变量）
            agent_config: Agent 配置（从 YAML）
        """
        self._configure(llm_config, agent_config, checkpointer)
        self.capabilities = (
            self._load_capabilities() if capabilities is None else list(capabilities)
        )
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
        self.hooks: list[Hook] = []
        self._started_hooks: list[Hook] = []
        self._start_task: asyncio.Task[None] | None = None
        self._session_ids: set[str] = set()
        self._session_close_tasks: dict[str, asyncio.Task[None]] = {}
        self._running = False

    def _assemble(self) -> None:
        self.tools = self._collect_tools()
        self.hooks = self._collect_hooks()
        self.trace_hook = TraceHook()
        self.hooks.insert(0, self.trace_hook)
        self.loop = KernelLoop(llm=self.llm, tools=self.tools, hooks=self.hooks)

    @classmethod
    async def create(
        cls,
        llm_config: LLMConfig,
        agent_config: AgentConfig,
        *,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        capabilities: list[Capability] | None = None,
    ) -> "AgentEngine":
        """Start capabilities before collecting tools; roll back on failure/cancellation."""
        engine = cls.__new__(cls)
        engine._configure(llm_config, agent_config, checkpointer)
        try:
            engine.capabilities = (
                engine._load_capabilities() if capabilities is None else list(capabilities)
            )
            engine.llm = engine._init_llm()
            for capability in engine.capabilities:
                await capability.start()
            engine._assemble()
            await engine.start()
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
        if self._start_task is not None:
            if not self._start_task.done():
                self._start_task.cancel()
            await asyncio.gather(self._start_task, return_exceptions=True)
        for session_id in list(self._session_ids):
            try:
                await self.close_session(session_id)
            except (Exception, asyncio.CancelledError) as exc:
                interrupted = True
                logging.getLogger(__name__).warning(
                    "Session cleanup failed (%s)", type(exc).__name__
                )
        for hook in reversed(self._started_hooks):
            try:
                if hasattr(hook, "on_stop"):
                    await hook.on_stop()
            except (Exception, asyncio.CancelledError) as exc:
                interrupted = True
                logging.getLogger(__name__).warning("Hook cleanup failed (%s)", type(exc).__name__)
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

    async def start(self) -> None:
        """Start hooks once; synchronous construction defers this to first run."""
        if self._closed:
            raise RuntimeError("Engine is closed")
        if self._start_task is None:
            self._start_task = asyncio.create_task(self._start_hooks())
        try:
            await asyncio.shield(self._start_task)
            if self._closed:
                raise RuntimeError("Engine is closed")
        except BaseException:
            try:
                await self.close()
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Hook startup rollback failed (%s)", type(exc).__name__
                )
            raise

    async def _start_hooks(self) -> None:
        config = {
            "capabilities": list(self.agent_config.capabilities),
            "capability_config": self.agent_config.capability_config,
            "tools": list(self.agent_config.tools),
            "system_prompt": self.agent_config.system_prompt,
            "llm": self.agent_config.llm_config,
        }
        for hook in self.hooks:
            self._started_hooks.append(hook)
            if hasattr(hook, "on_start"):
                await hook.on_start(deepcopy(config))

    async def load_session(self, state: AgentState) -> AgentState:
        """Load before adding new input; caller identities remain authoritative."""
        await self.start()
        session_id = runtime_context(state, create=True).setdefault("thread_id", str(uuid.uuid4()))
        if session_id in self._session_close_tasks:
            raise RuntimeError("Session is closed")
        self._session_ids.add(session_id)
        # Snapshot identities before plugin callbacks can affect their own stores.
        identity = {key: state["context"].get(key) for key in ("thread_id", "tenant_id", "user_id")}
        for hook in self.hooks:
            if not hasattr(hook, "on_session_load"):
                continue
            loaded = await hook.on_session_load(session_id)
            if loaded is None:
                continue
            if not isinstance(loaded, dict) or not isinstance(loaded.get("messages"), list):
                raise ValueError("Invalid restored AgentState")
            if not all(isinstance(message, BaseMessage) for message in loaded["messages"]):
                raise ValueError("Invalid restored messages")
            context = loaded.get("context", {})
            if not isinstance(context, dict):
                raise ValueError("Invalid restored context")
            for key, expected in identity.items():
                if key in context and context[key] != expected:
                    raise ValueError(f"Restored session identity conflict: {key}")
            if "tenant_id" in loaded and loaded["tenant_id"] != state.get("tenant_id"):
                raise ValueError("Restored session identity conflict: top-level tenant_id")
            restored = deepcopy(loaded)
            restored["context"] = {**restored.get("context", {}), **state["context"]}
            if "tenant_id" in state:
                restored["tenant_id"] = state["tenant_id"]
            return restored
        return state

    async def close_session(self, session_id: str) -> None:
        """Notify every hook once per session, draining cleanup on cancellation."""

        async def notify() -> None:
            failed = False
            for hook in reversed(self.hooks):
                try:
                    if hasattr(hook, "on_session_close"):
                        await hook.on_session_close(session_id)
                except (Exception, asyncio.CancelledError) as exc:
                    failed = True
                    logging.getLogger(__name__).warning(
                        "Session hook failed (%s)", type(exc).__name__
                    )
            self._session_ids.discard(session_id)
            if failed:
                raise RuntimeError("Session hook cleanup failed; remaining cleanup was attempted")

        if session_id not in self._session_close_tasks:
            self._session_close_tasks[session_id] = asyncio.create_task(notify())
        await wait_for_cleanup(self._session_close_tasks[session_id])

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
        configurations = self.agent_config.capability_config
        for name in self.agent_config.capabilities:
            # Explicit module paths always win. Entry point discovery loads only
            # this configured name, retaining configuration order and ownership.
            discovered = (
                {}
                if "module" in configurations.get(name, {})
                else discover_capabilities(
                    [name], configurations, strict=True, owned=self.capabilities
                )
            )
            if name not in discovered:
                self.capabilities.extend(PluginLoader().load([name], configurations))
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

    def _collect_hooks(self) -> list[Hook]:
        """收集所有 Capabilities 贡献的Hook.

        Returns:
            list[Hook]: Hook列表
        """
        hooks: list[Hook] = []
        for capability in self.capabilities:
            hooks.extend(capability.hooks())
        return hooks

    async def run(
        self,
        user_input: str,
        state: AgentState | None = None,
        *,
        max_tool_rounds: int = 8,
        stream: bool = False,
        on_event: EventSink | None = None,
    ) -> AgentState:
        """Execute one user turn, with initialization and restoration serialized."""
        if self._running:
            raise RuntimeError("AgentEngine supports one active conversation turn at a time")
        self._running = True
        try:
            return await self._run_turn(
                user_input, state, max_tool_rounds=max_tool_rounds, stream=stream, on_event=on_event
            )
        finally:
            self._running = False

    async def _run_turn(
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
        if self.trace_hook.current_trace is not None:
            raise RuntimeError("AgentEngine supports one active conversation turn at a time")
        # 初始化状态（如果是第一轮）
        if state is None:
            state = AgentState(messages=[])
            # 添加系统提示词（如果有）
            if self.agent_config.system_prompt:
                state["messages"].append(SystemMessage(content=self.agent_config.system_prompt))

        state = await self.load_session(state)
        # 添加用户消息
        state["messages"].append(HumanMessage(content=user_input))
        runtime_context(state, create=True).setdefault("thread_id", str(uuid.uuid4()))
        self._checkpoint_threads.add(runtime_context(state)["thread_id"])
        self.trace_hook.start(state)
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
            self.trace_hook.finish(frame.state, error=type(exc).__name__)
            raise
        finally:
            self.trace_hook.finish(frame.state)

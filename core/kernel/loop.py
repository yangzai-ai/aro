"""Core kernel loop - the minimal LLM loop.

这是内核的核心：一个最小的 LLM 循环。
它对任何具体能力（工具、知识库、记忆）零认知，只知道：
- 接收 messages
- 调用 LLM
- 如果 LLM 返回 tool_calls，执行工具
- 执行前后调用中间件

无工具、无中间件时，这就是一个裸 LLM 对话。
"""

import asyncio
import json
import time
import uuid
from collections.abc import Coroutine
from contextvars import copy_context
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, TypedDict

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from core.kernel.checkpoint import default_checkpointer
from core.kernel.events import emit_event
from core.kernel.middleware import Middleware
from core.kernel.state import AgentState, runtime_context
from core.kernel.streaming import collect_stream
from core.kernel.trace import TraceMiddleware
from core.llm.provider import LLMProvider
from core.tools.base import BaseTool, ToolResult


class _GraphState(TypedDict):
    # LastValue channels: never apply add_messages to a complete business state.
    business: AgentState
    calls: list[dict[str, Any]]
    rounds: int


@dataclass
class KernelRun:
    """Request-local latest state, including replacements before an exception.

    Internal to the kernel; never passed to capabilities or serialized as state.
    """

    state: AgentState


class KernelLoop:
    """最小 LLM 循环.

    职责：
    1. 执行前调用所有中间件的 before 钩子
    2. 调用 LLM
    3. 如果有 tool_calls，执行工具
    4. 执行后调用所有中间件的 after 钩子（逆序）

    这个循环对具体能力零认知：
    - 不知道有没有知识库
    - 不知道有没有记忆
    - 不知道有没有 HITL
    - 只知道"有一些工具"和"有一些中间件"

    能力通过提供工具和中间件来扩展循环，而不是修改循环本身。
    """

    def __init__(
        self,
        llm: LLMProvider,
        tools: list[BaseTool] | None = None,
        middlewares: list[Middleware] | None = None,
    ):
        """初始化内核循环.

        Args:
            llm: LLM Provider
            tools: 工具列表（可以为空）
            middlewares: 中间件列表（可以为空）
        """
        self.llm = llm
        self.tools = tools or []
        self.middlewares = middlewares or []

        if len({tool.name for tool in self.tools}) != len(self.tools):
            raise ValueError("Duplicate tool names are not allowed")

        # Forbidden tools are excluded from both advertising and execution.
        self._tool_map = {tool.name: tool for tool in self.tools if tool.permission != "forbidden"}

        # 过滤 forbidden 工具（不进 LLM 可调用列表）
        self._available_tools = [tool for tool in self.tools if tool.permission != "forbidden"]
        for middleware in self.middlewares:
            middleware.bind_tools(dict(self._tool_map))

    async def run_once(self, state: AgentState, *, stream: bool = False) -> AgentState:
        """Execute exactly one model round using the same graph as Engine.run."""
        return await self._run(KernelRun(state), stream=stream, single_round=True)

    async def _run(
        self,
        frame: KernelRun,
        *,
        max_tool_rounds: int = 8,
        stream: bool = False,
        single_round: bool = False,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
    ) -> AgentState:
        """Record snapshots; explicit input alone drives each fresh invocation."""
        thread_id = runtime_context(frame.state, create=True).setdefault(
            "thread_id", str(uuid.uuid4())
        )
        saver = default_checkpointer() if checkpointer is None else checkpointer
        # Nodes must share writes to ContextVars (identity, events, delegation).
        # Separate task contexts copied by LangGraph would lose these writes.
        context = copy_context()

        async def in_context(operation: Coroutine[Any, Any, _GraphState]) -> _GraphState:
            # Awaiting the owned task propagates cancellation and drains it before
            # the node can finish. No detached tool/approval task is left behind.
            return await asyncio.create_task(operation, context=context)

        async def agent(state: _GraphState) -> _GraphState:
            return await in_context(self._agent(state, frame, stream=stream))

        async def tools(state: _GraphState) -> _GraphState:
            return await in_context(self._tools(state, frame))

        async def after(state: _GraphState) -> _GraphState:
            return await in_context(self._after(state, frame))

        def next_round(values: _GraphState) -> str:
            if single_round or not isinstance(values["business"]["messages"][-1], ToolMessage):
                return END
            if values["rounds"] >= max_tool_rounds + 1:
                raise RuntimeError("Tool-call limit reached; simplify the question")
            return "agent"

        graph = StateGraph(_GraphState)
        graph.add_node("agent", agent)
        graph.add_node("tools", tools)
        graph.add_node("after", after)
        graph.add_edge(START, "agent")
        graph.add_conditional_edges(
            "agent",
            lambda values: "tools" if values["calls"] else "after",
            {"tools": "tools", "after": "after"},
        )
        graph.add_edge("tools", "after")
        graph.add_conditional_edges("after", next_round, {"agent": "agent", END: END})
        # Each complete model round uses at most three nodes. Leave margin so
        # LangGraph's generic recursion limit never replaces our business limit.
        await graph.compile(checkpointer=saver).ainvoke(
            _GraphState(business=frame.state, calls=[], rounds=0),
            config={
                "recursion_limit": 3 * (max_tool_rounds + 1) + 2,
                "configurable": {"thread_id": thread_id},
            },
        )
        return frame.state

    async def _agent(self, values: _GraphState, frame: KernelRun, *, stream: bool) -> _GraphState:
        state = frame.state = values["business"]
        # 1. Before 钩子（正序执行）
        for middleware in self.middlewares:
            state = await middleware.before(state)
            frame.state = state

        # 2. 调用 LLM
        # Convert the executable registry into function-calling schemas.
        llm_tools = self._convert_tools_to_langchain_format() if self._available_tools else None

        if stream:
            response = await collect_stream(self.llm, state["messages"], llm_tools)
        else:
            response = await self.llm.chat(messages=state["messages"], tools=llm_tools)
            if response.reasoning_content:
                await emit_event("reasoning", delta=response.reasoning_content)

        # 3. 添加 AI 响应到状态
        ai_message = AIMessage(content=response.content)
        if response.tool_calls:
            ai_message.tool_calls = response.tool_calls  # type: ignore
        # 保存 reasoning_content 到 additional_kwargs（不进 content）
        if response.reasoning_content:
            ai_message.additional_kwargs["reasoning_content"] = response.reasoning_content
        state["messages"].append(ai_message)

        return _GraphState(business=state, calls=response.tool_calls or [], rounds=values["rounds"])

    async def _tools(self, values: _GraphState, frame: KernelRun) -> _GraphState:
        state = frame.state = values["business"]
        # Freeze the entire batch before any hook or event sink can observe it.
        for tool_call in deepcopy(values["calls"]):
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            tool_id = tool_call.get("id", "")
            started = time.perf_counter()
            wall_started = time.time()
            trace_offsets = {
                middleware: len(middleware.current_trace.tool_calls)
                for middleware in self.middlewares
                if isinstance(middleware, TraceMiddleware) and middleware.current_trace is not None
            }
            runtime_context(state, create=True)["tool_call_id"] = tool_id
            await emit_event(
                "tool_call", call_id=tool_id, tool_name=tool_name, args=deepcopy(tool_args)
            )

            # 工具级中间件可以在执行前拦截调用。
            should_execute = True
            error_message: str | None = None
            tool = self._tool_map.get(tool_name)
            approval_duration_ms: float | None = None
            execution_duration_ms = 0.0
            authorization_started = time.perf_counter()
            try:
                for middleware in self.middlewares:
                    if tool and tool.permission == "write" and middleware._handles_write:
                        approval_duration_ms = 0.0
                    allowed, message = await middleware.before_tool(
                        tool_name, deepcopy(tool_args), state
                    )
                    if not allowed:
                        should_execute, error_message = False, message
                        break
            except Exception as exc:
                should_execute = False
                error_message = f"Tool authorization failed: {exc}"

            finally:
                if approval_duration_ms is not None:
                    approval_duration_ms = (time.perf_counter() - authorization_started) * 1000

            result: ToolResult
            if not should_execute:
                result = ToolResult(success=False, error=error_message or "Tool execution blocked")
                tool_message = ToolMessage(
                    content=f"Error: {result.error}", tool_call_id=tool_id, name=tool_name
                )
            elif (
                tool
                and tool.permission == "write"
                and not any(middleware._handles_write for middleware in self.middlewares)
            ):
                result = ToolResult(
                    success=False,
                    error="write operations require HITL capability, but it is not enabled",
                )
                tool_message = ToolMessage(
                    content=f"Error: {result.error}",
                    tool_call_id=tool_id,
                    name=tool_name,
                )
            elif tool:
                try:
                    execution_args = deepcopy(tool_args)
                    exec_started = time.perf_counter()
                    try:
                        async with asyncio.timeout(tool.timeout_seconds):
                            result = await tool.run(**execution_args)
                    finally:
                        execution_duration_ms = (time.perf_counter() - exec_started) * 1000
                    result = deepcopy(result)
                    tool_message = ToolMessage(
                        content=(
                            result.output
                            if isinstance(result.output, str)
                            else json.dumps(result.output, ensure_ascii=False)
                        )
                        if result.success
                        else f"Error: {result.error}",
                        tool_call_id=tool_id,
                        name=tool_name,
                    )
                except TimeoutError:
                    result = ToolResult(success=False, error="Tool execution timed out")
                    tool_message = ToolMessage(
                        content=f"Error: {result.error}", tool_call_id=tool_id, name=tool_name
                    )
                except Exception as e:
                    result = ToolResult(success=False, error=str(e))
                    tool_message = ToolMessage(
                        content=f"Tool execution failed: {e}",
                        tool_call_id=tool_id,
                        name=tool_name,
                    )
            else:
                result = ToolResult(success=False, error=f"Tool '{tool_name}' not found")
                tool_message = ToolMessage(
                    content=f"Tool '{tool_name}' not found",
                    tool_call_id=tool_id,
                    name=tool_name,
                )

            state["messages"].append(tool_message)
            middleware_errors: list[dict[str, str]] = []
            for middleware in self.middlewares:
                # Preserve canonical message identity (including delegation identity).
                # context stays shared: persistence/observability writes are permitted.
                messages = state["messages"]
                try:
                    view = state.copy()
                    view["messages"] = deepcopy(messages)
                    updated = await middleware.after_tool(
                        tool_name, deepcopy(tool_args), deepcopy(result), view
                    )
                    state = updated.copy()
                    state["messages"] = messages
                    frame.state = state
                except Exception as exc:
                    # Never persist raw exception text: it can contain credentials.
                    middleware_errors.append(
                        {
                            "middleware": type(middleware).__name__,
                            "hook": "after_tool",
                            "error_type": type(exc).__name__,
                        }
                    )
            duration_ms = (time.perf_counter() - started) * 1000
            for trace, offset in trace_offsets.items():
                trace._complete_tool_call(
                    offset,
                    tool_name,
                    tool_args,
                    result,
                    tool_id,
                    wall_started,
                    duration_ms,
                    approval_duration_ms,
                    execution_duration_ms,
                    middleware_errors,
                )
            await emit_event(
                "tool_result",
                call_id=tool_id,
                tool_name=tool_name,
                args=deepcopy(tool_args),
                success=result.success,
                result=deepcopy(result.output) if result.success else None,
                error=result.error,
                duration_ms=duration_ms,
                approval_duration_ms=approval_duration_ms,
                execution_duration_ms=execution_duration_ms,
                middleware_errors=deepcopy(middleware_errors),
            )

        return _GraphState(business=state, calls=[], rounds=values["rounds"])

    async def _after(self, values: _GraphState, frame: KernelRun) -> _GraphState:
        state = frame.state = values["business"]
        for middleware in reversed(self.middlewares):
            state = await middleware.after(state)
            frame.state = state
        return _GraphState(business=state, calls=[], rounds=values["rounds"] + 1)

    def _convert_tools_to_langchain_format(self) -> list[Any]:
        """转换工具为 LangChain 格式."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": f"{tool.description}\n\n{tool.usage_prompt}",
                    "parameters": tool.schema
                    if isinstance(tool.schema, dict)
                    else tool.schema.model_json_schema(),
                },
            }
            for tool in self._available_tools
        ]

"""Execution trace models and hook for the agent kernel."""

import time
import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from core.kernel.hook import Hook
from core.kernel.state import AgentState, runtime_context
from core.tools.base import ToolResult


@dataclass
class ToolCall:
    """One tool invocation and its outcome."""

    tool_name: str
    args: dict[str, Any]
    result: Any = None
    success: bool = False
    error: str | None = None
    start_time: float = 0.0
    end_time: float = 0.0
    duration_ms: float = 0.0
    call_id: str = ""
    approval_duration_ms: float | None = None
    execution_duration_ms: float = 0.0
    hook_errors: list[dict[str, str]] = field(default_factory=list)


@dataclass
class AgentTrace:
    """A complete kernel turn trace."""

    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    thread_id: str | None = None
    user_input: str = ""
    agent_response: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    duration_ms: float | None = None
    error: str | None = None

    def finalize(self) -> None:
        """Record the end timestamp and total duration."""
        self.end_time = time.time()
        self.duration_ms = (self.end_time - self.start_time) * 1000

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        data = asdict(self)
        data["tool_calls"] = [asdict(call) for call in self.tool_calls]
        return data


class TraceHook(Hook):
    """Collect one trace for each sequential kernel turn."""

    def __init__(self) -> None:
        self.current_trace: AgentTrace | None = None
        self.traces: list[AgentTrace] = []
        self._tool_start_times: dict[str, list[float]] = {}
        self._standalone = False
        self._message_offset = 0

    def start(self, state: AgentState) -> None:
        """Start one user turn; the engine finishes it after all model rounds."""
        self._standalone = False
        self._message_offset = len(state["messages"])
        self._tool_start_times.clear()
        trace = AgentTrace()
        context = runtime_context(state, create=True)
        trace.thread_id = context.get("thread_id")
        humans = [message for message in state["messages"] if isinstance(message, HumanMessage)]
        if humans:
            trace.user_input = str(humans[-1].content)
        context["trace_id"] = trace.trace_id
        self.current_trace = trace

    def finish(self, state: AgentState, error: str | None = None) -> None:
        """Finalize a trace exactly once, including interrupted/failed turns."""
        if self.current_trace is None:
            return
        assistants = [
            message
            for message in state["messages"][self._message_offset :]
            if isinstance(message, AIMessage) and not message.tool_calls
        ]
        if assistants:
            self.current_trace.agent_response = str(assistants[-1].content or "")
        self.current_trace.error = error
        self.current_trace.finalize()
        self.traces.append(self.current_trace)
        self.current_trace = None
        self._tool_start_times.clear()

    async def before(self, state: AgentState) -> AgentState:
        """Start a trace and capture the latest user message."""
        if self.current_trace is None:
            self.start(state)
            self._standalone = True
        return state

    async def after(self, state: AgentState) -> AgentState:
        """Capture the latest assistant message and finish the trace."""
        if self._standalone:
            self.finish(state)
        return state

    async def before_tool(
        self, tool_name: str, tool_args: dict[str, Any], state: AgentState
    ) -> tuple[bool, str | None]:
        """Record a start timestamp for a tool call."""
        self._tool_start_times.setdefault(tool_name, []).append(time.time())
        return True, None

    async def after_tool(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_result: ToolResult,
        state: AgentState,
    ) -> AgentState:
        """Append a tool result to the active trace."""
        if self.current_trace is None:
            return state
        starts = self._tool_start_times.get(tool_name, [])
        start = starts.pop(0) if starts else time.time()
        end = time.time()
        self.current_trace.tool_calls.append(
            ToolCall(
                tool_name=tool_name,
                args=deepcopy(tool_args),
                result=deepcopy(tool_result.output) if tool_result.success else None,
                success=tool_result.success,
                error=tool_result.error if not tool_result.success else None,
                start_time=start,
                end_time=end,
                duration_ms=(end - start) * 1000,
                call_id=runtime_context(state).get("tool_call_id", ""),
            )
        )
        return state

    def _complete_tool_call(
        self,
        offset: int,
        tool_name: str,
        args: dict[str, Any],
        result: ToolResult,
        call_id: str,
        start_time: float,
        duration_ms: float,
        approval_duration_ms: float | None,
        execution_duration_ms: float,
        hook_errors: list[dict[str, str]],
    ) -> None:
        """Kernel finalization after all observers, including failing observers.

        Existing hooks stay unchanged. Fall back to a canonical record if this
        observer's hook failed; match by batch position, not possibly absent IDs.
        """
        if self.current_trace is None:
            return
        calls = self.current_trace.tool_calls
        call = ToolCall(tool_name=tool_name, args=deepcopy(args))
        call.result = deepcopy(result.output) if result.success else None
        call.success = result.success
        call.error = result.error if not result.success else None
        call.start_time = start_time
        call.end_time = time.time()
        call.duration_ms = duration_ms
        call.call_id = call_id
        call.approval_duration_ms = approval_duration_ms
        call.execution_duration_ms = execution_duration_ms
        call.hook_errors = deepcopy(hook_errors)
        if len(calls) > offset:
            calls[offset] = call
        else:
            calls.append(call)

    def get_latest_trace(self) -> AgentTrace | None:
        """Return the most recently completed trace."""
        return self.traces[-1] if self.traces else None

    def get_all_traces(self) -> list[AgentTrace]:
        """Return completed traces in order."""
        return list(self.traces)


def print_trace(trace: AgentTrace) -> None:
    """Print a compact human-readable trace for the CLI."""
    print("\n" + "=" * 60)
    print(f"Agent Trace: {trace.trace_id}")
    print(f"User: {trace.user_input}")
    for index, call in enumerate(trace.tool_calls, 1):
        status = "OK" if call.success else "FAILED"
        print(f"  {index}. [{status}] {call.tool_name} ({call.duration_ms:.2f}ms)")
        print(f"     Args: {call.args}")
        print(f"     Result: {call.result if call.success else call.error}")
    print(f"Agent: {trace.agent_response}")
    if trace.error:
        print(f"Error: {trace.error}")
    if trace.duration_ms is not None:
        print(f"Total Duration: {trace.duration_ms:.2f}ms")
    print("=" * 60)

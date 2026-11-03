"""Agent state definition.

AgentState 是 LangGraph 循环的核心数据结构。
它必须在最早定义，因为所有模块（loop、middleware、tools）都依赖它。
"""

from typing import Annotated, Any, Required, TypedDict, cast

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class RuntimeContext(TypedDict, total=False):
    """Known runtime fields; optional identities retain their existing semantics.

    This describes kernel writes, not runtime identity validation. Legacy input
    dictionaries and their unknown keys remain intact during this transition.
    """

    thread_id: str
    tenant_id: str | None
    user_id: str | None
    trace_id: str
    tool_call_id: str
    parent_thread_id: str
    delegation_depth: int
    extra: dict[str, Any]


class AgentState(TypedDict, total=False):
    """Agent 的状态结构.

    这是 LangGraph 的状态容器，在 Agent 执行过程中流转。

    Attributes:
        messages: 对话消息列表（user/assistant/tool），使用 add_messages reducer 自动累加
    """

    messages: Required[Annotated[list[BaseMessage], add_messages]]
    context: dict[str, Any]
    tenant_id: str | None


def runtime_context(state: AgentState, *, create: bool = False) -> RuntimeContext:
    """Access known fields without copying, coercing or filtering legacy input.

    Reads of missing context do not change state. Writers explicitly request a
    typed empty context. Casts are confined to this legacy mapping boundary;
    they perform no validation and are not proof of a trusted identity.
    """
    if create and "context" not in state:
        state["context"] = cast(dict[str, Any], RuntimeContext())
    return cast(RuntimeContext, state.get("context", RuntimeContext()))

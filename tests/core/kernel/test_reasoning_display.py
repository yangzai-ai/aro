"""Reasoning display: events, AIMessage storage, protocol passback, isolation."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from core.kernel.events import ExecutionEvent, event_scope
from core.kernel.loop import KernelLoop
from core.kernel.state import AgentState
from core.kernel.streaming import collect_stream
from core.llm.openai_provider import OpenAICompatibleProvider
from tests.test_kernel_loop import FakeLLMProvider


class ReasoningProvider(FakeLLMProvider):
    """Emit reasoning_content/reasoning_delta."""

    def __init__(self, reasoning: str, content: str = "answer"):
        super().__init__(content)
        self.reasoning = reasoning

    async def chat(self, **kwargs):
        response = await super().chat(**kwargs)
        response.reasoning_content = self.reasoning
        return response

    async def chat_stream(self, **kwargs):
        yield {"delta": "ans", "reasoning_delta": self.reasoning[:2]}
        yield {"delta": "wer", "reasoning_delta": self.reasoning[2:]}
        yield {"finish_reason": "stop"}


async def test_reasoning_event_emitted_in_stream():
    events = []

    async def sink(event: ExecutionEvent) -> None:
        events.append(event)

    provider = ReasoningProvider("思考内容")
    with event_scope(sink):
        response = await collect_stream(provider, [], None)
    assert response.content == "answer"
    assert response.reasoning_content == "思考内容"
    assert len(events) == 4  # token x2, reasoning x2
    assert events[0].type == "reasoning" and events[0].data["delta"] == "思考"
    assert events[1].type == "token" and events[1].data["delta"] == "ans"
    assert events[2].type == "reasoning" and events[2].data["delta"] == "内容"
    assert events[3].type == "token" and events[3].data["delta"] == "wer"


async def test_reasoning_stored_in_additional_kwargs_not_content():
    provider = ReasoningProvider("thinking")
    loop = KernelLoop(provider, [])
    state = await loop.run_once(AgentState(messages=[HumanMessage(content="hi")]), stream=True)
    ai = state["messages"][-1]
    assert isinstance(ai, AIMessage)
    assert ai.content == "answer"
    assert ai.additional_kwargs.get("reasoning_content") == "thinking"


async def test_no_reasoning_no_block():
    """Non-reasoning model does not emit reasoning event or store anything."""
    events = []

    async def sink(event: ExecutionEvent) -> None:
        events.append(event)

    provider = ReasoningProvider("", "plain")
    with event_scope(sink):
        response = await collect_stream(provider, [], None)
    assert response.content == "answer"
    assert response.reasoning_content is None
    # Only token event
    reasoning_events = [e for e in events if e.type == "reasoning"]
    assert len(reasoning_events) == 0


def test_deepseek_model_detection():
    """_convert_messages identifies DeepSeek and passes reasoning_content when tools present."""
    provider = OpenAICompatibleProvider(
        api_key="test", base_url="http://test", model="deepseek-v4-pro", enable_thinking=True
    )
    # Create messages with reasoning in additional_kwargs
    ai_with_reasoning = AIMessage(content="response")
    ai_with_reasoning.additional_kwargs["reasoning_content"] = "my_thinking"

    messages = [HumanMessage(content="q"), ai_with_reasoning]

    # Without tools: reasoning should NOT be passed back
    converted = provider._convert_messages(messages, tools_present=False)
    assistant_msgs = [m for m in converted if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1
    assert "reasoning_content" not in assistant_msgs[0]

    # With tools: reasoning SHOULD be passed back
    converted_with_tools = provider._convert_messages(messages, tools_present=True)
    assistant_msgs_tools = [m for m in converted_with_tools if m["role"] == "assistant"]
    assert len(assistant_msgs_tools) == 1
    assert assistant_msgs_tools[0].get("reasoning_content") == "my_thinking"


def test_non_deepseek_never_passes_reasoning():
    """Other models never pass reasoning_content even with tools."""
    provider = OpenAICompatibleProvider(
        api_key="test", base_url="http://test", model="gpt-4", enable_thinking=True
    )
    ai_with_reasoning = AIMessage(content="response")
    ai_with_reasoning.additional_kwargs["reasoning_content"] = "thinking"

    messages = [HumanMessage(content="q"), ai_with_reasoning]
    converted = provider._convert_messages(messages, tools_present=True)
    assistant_msgs = [m for m in converted if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1
    assert "reasoning_content" not in assistant_msgs[0]


@pytest.mark.parametrize("enable_thinking", [True, False])
def test_thinking_disabled_no_passback(enable_thinking):
    """DeepSeek with enable_thinking=False does not pass reasoning back."""
    provider = OpenAICompatibleProvider(
        api_key="test", base_url="http://test", model="deepseek-v4", enable_thinking=enable_thinking
    )
    ai_with_reasoning = AIMessage(content="response")
    ai_with_reasoning.additional_kwargs["reasoning_content"] = "thinking"

    messages = [HumanMessage(content="q"), ai_with_reasoning]
    converted = provider._convert_messages(messages, tools_present=True)
    assistant_msgs = [m for m in converted if m["role"] == "assistant"]
    # Only pass back when thinking is enabled
    if enable_thinking:
        assert assistant_msgs[0].get("reasoning_content") == "thinking"
    else:
        assert "reasoning_content" not in assistant_msgs[0]

@pytest.mark.parametrize("stream", [False, True])
async def test_reasoning_trace_isolation_and_nonstream_event(tmp_path, stream):
    from tests.capabilities.multiagent.test_orchestration import make_engine

    engine = make_engine(tmp_path)
    engine.loop.llm = ReasoningProvider("PRIVATE-THINKING")
    events = []

    async def sink(event):
        events.append(event)

    try:
        state = await engine.run("hello", stream=stream, on_event=sink)
        assert state["messages"][-1].content == "answer"
        assert engine.trace_middleware.get_latest_trace().agent_response == "answer"
        assert "PRIVATE-THINKING" not in str(engine.trace_middleware.get_latest_trace().to_dict())
        thinking = [e.data["delta"] for e in events if e.type == "reasoning"]
        assert "".join(thinking) == "PRIVATE-THINKING"
        if not stream:
            assert len(thinking) == 1
    finally:
        await engine.close()


async def test_delegated_reasoning_is_separate_and_source_labelled(tmp_path):
    from contextlib import asynccontextmanager

    from capabilities.multiagent.handle import AgentContext, LocalAgentHandle
    from tests.capabilities.multiagent.test_orchestration import make_engine

    @asynccontextmanager
    async def scope():
        engine = make_engine(tmp_path)
        engine.loop.llm = ReasoningProvider("child thinking")
        try:
            yield engine
        finally:
            await engine.close()

    events = []

    async def sink(event):
        events.append(event)

    with event_scope(sink):
        result = await LocalAgentHandle("analyst", "read only", scope).invoke(
            "hello", AgentContext(thread_id="parent", parent_call_id="call-1")
        )
    assert result.answer == "answer"
    assert result.trace["agent_response"] == "answer"
    assert not any(e.type in {"reasoning", "token"} for e in events)
    thinking = [e for e in events if e.type == "agent_reasoning"]
    assert "".join(e.data["delta"] for e in thinking) == "child thinking"
    assert all(e.data["agent_name"] == "analyst" and e.data["parent_call_id"] == "call-1" for e in thinking)


@pytest.mark.parametrize("kind", ["reasoning", "agent_reasoning"])
def test_reasoning_encode_event_redacts_secrets(kind):
    from api.sse import encode_event

    frame = encode_event(ExecutionEvent(type=kind, data={"delta": "key-secret https://u:p@host/db\nevent: done"}), ("key-secret",))
    assert "key-secret" not in frame and "u:p" not in frame
    assert "[redacted]" in frame
    assert frame.count("\nevent:") == 0


@pytest.mark.parametrize("model", ["deepseek-v4-pro", "gpt-4"])
@pytest.mark.parametrize("thinking", [True, False])
@pytest.mark.parametrize("tool_input", ["valid", "empty", "discarded", "none"])
@pytest.mark.parametrize("stream", [True, False])
async def test_actual_request_protocol_gate(model, thinking, tool_input, stream):
    import json

    import httpx
    from openai import AsyncOpenAI

    requests = []

    def upstream(request):
        requests.append(json.loads(request.content))
        if stream:
            chunks = [
                {"choices": [{"delta": {"content": "ok"}, "finish_reason": None}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            ]
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  text="".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n")
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}], "usage": None})

    provider = OpenAICompatibleProvider(api_key="test", base_url="https://test.invalid/v1", model=model, enable_thinking=thinking)
    await provider._client.close()
    provider._client = AsyncOpenAI(api_key="test", base_url="https://test.invalid/v1", http_client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    with_tools = tool_input == "valid"
    tools = {
        "valid": [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object", "properties": {}}}}],
        "empty": [],
        "discarded": [object()],  # Nonempty input, but conversion sends no tools (R1).
        "none": None,
    }[tool_input]
    messages = [HumanMessage(content="q"), AIMessage(content="answer", additional_kwargs={"reasoning_content": "THINK-SENTINEL"})]
    try:
        if stream:
            async for _ in provider.chat_stream(messages, tools=tools):
                pass
        else:
            await provider.chat(messages, tools=tools)
    finally:
        await provider._client.close()
    payload = requests[0]
    assert ("tools" in payload) == with_tools
    ai = payload["messages"][-1]
    assert ai["content"] == "answer"
    assert (ai.get("reasoning_content") == "THINK-SENTINEL") == (model.startswith("deepseek") and thinking and with_tools)
    assert "THINK-SENTINEL" not in str(payload.get("tools"))
    assert all("THINK-SENTINEL" not in m["content"] for m in payload["messages"])

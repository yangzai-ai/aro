"""Assemble indexed tool argument deltas only after a complete model stream."""

import json
import uuid
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage

from core.kernel.events import emit_event
from core.llm.provider import LLMProvider, LLMResponse


@dataclass
class PartialToolCall:
    """One indexed function call under construction."""

    id: str = ""
    name: str = ""
    arguments: str = ""


async def collect_stream(
    provider: LLMProvider, messages: list[BaseMessage], tools: list[Any] | None
) -> LLMResponse:
    """Forward real tokens and safely assemble all interleaved tool calls."""
    text: list[str] = []
    calls: dict[int, PartialToolCall] = {}
    finish: str | None = None
    stream = provider.chat_stream(messages=messages, tools=tools)
    try:
        async for chunk in stream:
            delta = chunk.get("delta", "")
            if delta:
                text.append(delta)
                await emit_event("token", delta=delta)
            for part in chunk.get("tool_calls_delta") or []:
                index = part.get("index", 0)
                if not isinstance(index, int) or not 0 <= index < 128:
                    raise ValueError("Invalid tool call stream index")
                call = calls.setdefault(index, PartialToolCall())
                call.id += part.get("id") or ""
                function = part.get("function") or {}
                call.name += function.get("name") or ""
                call.arguments += function.get("arguments") or ""
                if len(call.arguments) > 1_000_000:
                    raise ValueError("Tool call arguments exceed the stream limit")
            if chunk.get("finish_reason"):
                finish = chunk["finish_reason"]
    finally:
        # Async generators (including the HTTP-backed provider) release sockets.
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()
    if finish not in {"stop", "tool_calls"}:
        raise ValueError("Model stream ended without a complete response")
    if calls and finish != "tool_calls":
        raise ValueError("Model stream did not finish its tool calls")
    complete: list[dict[str, Any]] = []
    for index in sorted(calls):
        call = calls[index]
        if not call.name:
            raise ValueError("Streamed tool call has no name")
        args = json.loads(call.arguments or "{}")
        if not isinstance(args, dict):
            raise ValueError("Tool arguments must be a JSON object")
        complete.append({"id": call.id or str(uuid.uuid4()), "name": call.name, "args": args})
    return LLMResponse(content="".join(text), tool_calls=complete or None)

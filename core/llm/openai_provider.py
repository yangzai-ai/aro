"""OpenAI-compatible LLM provider implementation.

统一适配器，支持所有 OpenAI 兼容的提供商：
- DeepSeek (https://api.deepseek.com/v1) — 默认优先
- OpenAI (https://api.openai.com/v1)
- 中转站
- Ollama (http://localhost:11434/v1) — 本地预留

只需切换 base_url 和 api_key 即可切换提供商。
"""

import json
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import BaseMessage
from openai import AsyncOpenAI

from core.llm.provider import (
    LLMAuthenticationError,
    LLMError,
    LLMProvider,
    LLMRateLimitError,
    LLMResponse,
    LLMTimeoutError,
)


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI 兼容提供商实现（统一使用原生 SDK）."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = 0.7,
        timeout: float = 60.0,
        enable_thinking: bool = True,
    ):
        """初始化."""
        self.model = model
        self.default_temperature = temperature
        self.enable_thinking = enable_thinking

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )

    async def chat(
        self,
        messages: list[BaseMessage],
        tools: list[Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """调用 LLM."""
        try:
            # Gate protocol metadata on the tools actually sent, after conversion.
            openai_tools = self._convert_tools(tools) if tools else None
            openai_messages = self._convert_messages(messages, tools_present=bool(openai_tools))

            # 构造请求
            request_params: dict[str, Any] = {
                "model": self.model,
                "messages": openai_messages,
                "temperature": temperature or self.default_temperature,
            }

            if max_tokens:
                request_params["max_tokens"] = max_tokens

            if openai_tools:
                request_params["tools"] = openai_tools

            # 添加 thinking 参数
            if self.enable_thinking:
                request_params["extra_body"] = {"thinking": {"type": "enabled"}}

            # 调用 API
            response = await self._client.chat.completions.create(**request_params)

            # 提取响应
            return self._extract_response(response)

        except Exception as e:
            error_msg = str(e).lower()
            if "unauthorized" in error_msg or "invalid_api_key" in error_msg:
                raise LLMAuthenticationError(f"Authentication failed: {e}") from e
            elif "rate_limit" in error_msg or "429" in error_msg:
                raise LLMRateLimitError(f"Rate limit exceeded: {e}") from e
            elif "timeout" in error_msg:
                raise LLMTimeoutError(f"Request timeout: {e}") from e
            else:
                raise LLMError(f"LLM call failed: {e}") from e

    def _convert_messages(
        self, messages: list[BaseMessage], tools_present: bool = False
    ) -> list[dict[str, Any]]:
        """转换 LangChain 消息为 OpenAI 格式.

        Args:
            messages: LangChain 消息列表
            tools_present: 本次请求是否携带 tools 参数
        """
        # 识别 DeepSeek：模型名包含 deepseek
        is_deepseek = "deepseek" in self.model.lower()
        # DeepSeek 思考模式 + tools 要求回传 reasoning_content
        should_pass_reasoning = is_deepseek and self.enable_thinking and tools_present

        openai_messages = []
        for msg in messages:
            if not hasattr(msg, "type"):
                continue

            if msg.type == "system":
                openai_messages.append({"role": "system", "content": msg.content})
            elif msg.type == "human":
                openai_messages.append({"role": "user", "content": msg.content})
            elif msg.type == "ai":
                # 确保 content 不为 None 或空字符串
                ai_content = msg.content if msg.content else " "
                ai_msg: dict[str, Any] = {"role": "assistant", "content": ai_content}
                # LangChain 的 tool_calls 格式需要转换为 OpenAI 格式
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    converted_tool_calls = []
                    for tc in msg.tool_calls:
                        if isinstance(tc, dict):
                            converted_tool_calls.append(
                                {
                                    "id": tc.get("id", ""),
                                    "type": "function",
                                    "function": {
                                        "name": tc.get("name", ""),
                                        "arguments": json.dumps(tc.get("args", {})),
                                    },
                                }
                            )
                    if converted_tool_calls:
                        ai_msg["tool_calls"] = converted_tool_calls

                # DeepSeek 思考模式 + tools：按协议回传 reasoning_content
                if should_pass_reasoning:
                    reasoning = (
                        msg.additional_kwargs.get("reasoning_content")
                        if hasattr(msg, "additional_kwargs")
                        else None
                    )
                    if reasoning:
                        ai_msg["reasoning_content"] = reasoning

                openai_messages.append(ai_msg)
            elif msg.type == "tool":
                tool_content = msg.content if msg.content else " "  # ← 修复：确保工具消息也不为空
                openai_messages.append(
                    {
                        "role": "tool",
                        "content": tool_content,
                        "tool_call_id": getattr(msg, "tool_call_id", ""),
                    }
                )

        return openai_messages

    def _convert_tools(self, tools: list[Any]) -> list[dict[str, Any]]:
        """转换工具格式."""
        openai_tools = []
        for tool in tools:
            if hasattr(tool, "to_dict"):
                openai_tools.append(tool.to_dict())
            elif isinstance(tool, dict):
                openai_tools.append(tool)
        return openai_tools

    def _extract_response(self, response: Any) -> LLMResponse:
        """提取响应."""
        message = response.choices[0].message
        content = message.content or ""

        # 提取 reasoning_content
        reasoning_content = None
        if hasattr(message, "reasoning_content"):
            reasoning_content = message.reasoning_content
        elif hasattr(message, "model_dump"):
            try:
                dumped = message.model_dump()
                reasoning_content = dumped.get("reasoning_content")
            except (AttributeError, TypeError, ValueError):
                pass

        # 提取 tool_calls
        tool_calls = None
        if hasattr(message, "tool_calls") and message.tool_calls:
            tool_calls = []
            for tc in message.tool_calls:
                # 处理 tool_calls 的参数（可能是字符串）
                args = {}
                if hasattr(tc, "function"):
                    args_str = tc.function.arguments
                    if isinstance(args_str, str):
                        try:
                            args = json.loads(args_str)
                        except (TypeError, json.JSONDecodeError):
                            args = {}
                    else:
                        args = args_str or {}

                    tool_calls.append(
                        {
                            "id": tc.id,
                            "name": tc.function.name,
                            "args": args,
                        }
                    )

        # 提取 usage
        usage = None
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        return LLMResponse(
            content=content,
            reasoning_content=reasoning_content,
            tool_calls=tool_calls,
            usage=usage,
        )

    async def chat_stream(
        self,
        messages: list[BaseMessage],
        tools: list[Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式调用 LLM.

        Yields:
            dict: 包含 delta 的增量数据
                - delta: str - 增量文本内容
                - reasoning_delta: str | None - 推理过程增量
                - tool_calls: list | None - 工具调用（完整）
                - finish_reason: str | None - 结束原因
        """
        try:
            # Gate protocol metadata on the tools actually sent, after conversion.
            openai_tools = self._convert_tools(tools) if tools else None
            openai_messages = self._convert_messages(messages, tools_present=bool(openai_tools))

            # 构造请求
            request_params: dict[str, Any] = {
                "model": self.model,
                "messages": openai_messages,
                "temperature": temperature or self.default_temperature,
                "stream": True,  # ← 关键：启用流式
            }

            if max_tokens:
                request_params["max_tokens"] = max_tokens

            if openai_tools:
                request_params["tools"] = openai_tools

            # 添加 thinking 参数
            if self.enable_thinking:
                request_params["extra_body"] = {"thinking": {"type": "enabled"}}

            # 调用流式 API
            stream = await self._client.chat.completions.create(**request_params)

            try:
                # 逐块处理
                async for chunk in stream:
                    if not chunk.choices:
                        continue

                    delta = chunk.choices[0].delta
                    finish_reason = chunk.choices[0].finish_reason

                    # 提取增量内容
                    content_delta = delta.content or ""
                    reasoning_delta = getattr(delta, "reasoning_content", None) or ""

                    # 提取工具调用（流式模式下是增量的）
                    tool_calls_delta = None
                    if hasattr(delta, "tool_calls") and delta.tool_calls:
                        tool_calls_delta = []
                        for tc in delta.tool_calls:
                            tc_dict = {
                                "index": getattr(tc, "index", 0),
                                "id": getattr(tc, "id", None),
                                "type": getattr(tc, "type", None),
                            }
                            if hasattr(tc, "function"):
                                tc_dict["function"] = {
                                    "name": getattr(tc.function, "name", None),
                                    "arguments": getattr(tc.function, "arguments", ""),
                                }
                            tool_calls_delta.append(tc_dict)

                    yield {
                        "delta": content_delta,
                        "reasoning_delta": reasoning_delta,
                        "tool_calls_delta": tool_calls_delta,  # ← 改为增量
                        "finish_reason": finish_reason,
                    }
            finally:
                await stream.close()

        except Exception as e:
            error_msg = str(e).lower()
            if "unauthorized" in error_msg or "invalid_api_key" in error_msg:
                raise LLMAuthenticationError(f"Authentication failed: {e}") from e
            elif "rate_limit" in error_msg or "429" in error_msg:
                raise LLMRateLimitError(f"Rate limit exceeded: {e}") from e
            elif "timeout" in error_msg:
                raise LLMTimeoutError(f"Request timeout: {e}") from e
            else:
                raise LLMError(f"LLM call failed: {e}") from e

"""LLM Provider contract.

统一的 LLM 调用接口，屏蔽不同 LLM 提供商的差异。
支持 OpenAI 兼容接口（DeepSeek / OpenAI / 中转站 / Ollama）。

设计要点：
- 统一接口：chat() 方法，输入消息列表，输出响应
- 工具调用：支持 tools 参数，返回 tool_calls
- 流式输出：支持 stream=True（v0.5 需要）
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage


@dataclass
class LLMResponse:
    """LLM 响应结果.

    Attributes:
        content: 文本内容
        reasoning_content: 推理过程（如 DeepSeek 的 CoT）
        tool_calls: 工具调用列表（如果 LLM 决定调用工具）
        usage: Token 使用情况（可选，用于成本统计）
    """

    content: str
    reasoning_content: str | None = None  # ← 新增：推理过程
    tool_calls: list[dict[str, Any]] | None = None
    usage: dict[str, int] | None = None


class LLMProvider(ABC):
    """LLM Provider 基类（契约）.

    所有 LLM 提供商必须实现此接口。

    支持的提供商：
    - OpenAI（原生）
    - DeepSeek（OpenAI 兼容，优先）
    - 中转站（OpenAI 兼容）
    - Ollama（OpenAI 兼容，本地预留）

    Example:
        provider = OpenAICompatibleProvider(
            api_key="your_key",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
        )

        response = await provider.chat(
            messages=[{"role": "user", "content": "Hello"}],
            tools=[tool1, tool2],
        )
    """

    @abstractmethod
    async def chat(
        self,
        messages: list[BaseMessage],
        tools: list[Any] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """调用 LLM.

        Args:
            messages: 消息列表（LangChain BaseMessage）
            tools: 可用工具列表（可选）
            temperature: 温度参数
            max_tokens: 最大生成 token 数
            **kwargs: 其他提供商特定参数

        Returns:
            LLMResponse: LLM 响应

        Raises:
            LLMError: LLM 调用失败（网络错误、认证失败等）
        """
        ...

    @abstractmethod
    def chat_stream(
        self,
        messages: list[BaseMessage],
        tools: list[Any] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式调用 LLM（v0.5 需要）.

        Args:
            messages: 消息列表
            tools: 可用工具列表（可选）
            temperature: 温度参数
            max_tokens: 最大生成 token 数
            **kwargs: 其他提供商特定参数

        Yields:
            str: 流式生成的文本 chunk

        Note:
            v0.1 可以先占位 raise NotImplementedError
            v0.5 实现（SSE 流式需要）
        """
        ...


class LLMError(Exception):
    """LLM 调用错误基类."""

    pass


class LLMAuthenticationError(LLMError):
    """认证失败（API Key 错误）."""

    pass


class LLMRateLimitError(LLMError):
    """速率限制（429）."""

    pass


class LLMTimeoutError(LLMError):
    """超时."""

    pass

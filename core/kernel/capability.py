"""Capability contract.

Capability 是能力的统一封装（打包器）。
它不是第三个扩展点——真正的扩展点只有两个：工具 + Hook。
Capability 的作用是把相关的工具和Hook打包成一个"能力"，方便配置和初始化。

例如：
- 知识库 Capability: 提供 search_knowledge 工具
- 记忆 Capability: 提供 search_memory 工具 + 记忆读写Hook
- HITL Capability: 提供审批Hook

能力是可插拔的：
- 配置中列出 capabilities: [memory, knowledge]
- 未列出的能力，代码完全不加载
- 内核对具体能力零认知
"""

from abc import ABC, abstractmethod
from typing import Any

from core.kernel.hook import Hook
from core.tools.base import BaseTool


class Capability(ABC):
    """能力基类（契约）.

    Capability 是"打包器"，把相关的工具和Hook组织成一个可配置的单元。

    设计要点：
    - 一个 Capability 可以只提供工具（如知识库）
    - 一个 Capability 可以只提供Hook（如 HITL）
    - 一个 Capability 可以同时提供工具和Hook（如记忆）
    - 未启用的 Capability，其代码完全不加载

    Example:
        class KnowledgeCapability(Capability):
            def __init__(self, config: dict):
                self.vector_store = QdrantClient(...)
                self.embedding_model = BGEEmbedding(...)

            @property
            def name(self) -> str:
                return "knowledge"

            def tools(self) -> list[BaseTool]:
                return [SearchKnowledgeTool(self.vector_store)]

            def hooks(self) -> list[Hook]:
                return []  # 知识库不需要Hook
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """能力名称（用于配置引用）."""
        ...

    @abstractmethod
    def tools(self) -> list[BaseTool]:
        """此能力提供的工具列表.

        Returns:
            list[BaseTool]: 工具列表（可以为空）
        """
        ...

    @abstractmethod
    def hooks(self) -> list[Hook]:
        """此能力提供的Hook列表.

        Returns:
            list[Hook]: Hook列表（可以为空）
        """
        ...

    def initialize(self, config: dict[str, Any]) -> None:  # noqa: B027
        """初始化能力（可选）.

        Args:
            config: 配置字典（来自 agent.yml 的 capability_config）

        Note:
            如果能力需要复杂初始化（连接数据库、加载模型等），
            在 __init__ 或此方法中完成。
            默认实现为空（子类可选择性覆盖）。
        """
        # 默认实现为空，子类可选择性覆盖

    async def start(self) -> None:  # noqa: B027
        """Optional async initialization, completed before tools are collected."""

    async def close(self) -> None:  # noqa: B027
        """Optional async cleanup; tolerate partially completed initialization."""

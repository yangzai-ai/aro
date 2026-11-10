"""Configuration loader.

从 YAML 和环境变量加载配置。
支持：
- agent.yml: 声明式配置（提示词、工具列表、能力列表）
- .env: 运行时密钥（API Key、数据库连接等）

设计要点：
- 严禁硬编码密钥、URL、模型名
- 配置与代码分离
- YAML 只做声明式配置，不表达流程逻辑
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings


class LLMConfig(BaseSettings):
    """LLM 配置（从环境变量加载）.

    Environment variables:
        OPENAI_API_KEY: API Key
        OPENAI_API_BASE: API Base URL
        DEFAULT_MODEL: 默认模型名
        DEFAULT_TEMPERATURE: 默认温度
    """

    api_key: str = Field(alias="OPENAI_API_KEY")
    api_base: str = Field(alias="OPENAI_API_BASE")
    model: str = Field(default="deepseek-chat", alias="DEFAULT_MODEL")
    temperature: float = Field(default=0.7, alias="DEFAULT_TEMPERATURE")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # 忽略额外的环境变量（如 LOG_LEVEL）


class AgentConfig:
    """Agent 配置（从 YAML 加载）.

    agent.yml 结构：
        system_prompt: "你是一个助手..."
        llm:
          model: deepseek-chat
          temperature: 0.7
        capabilities:
          - basic_tools
          - knowledge
        tools:
          - tool_name_1
          - tool_name_2
    """

    def __init__(self, config_path: Path | str):
        """从 YAML 文件加载配置.

        Args:
            config_path: agent.yml 路径
        """
        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ValueError("Agent configuration root must be a YAML mapping")
        self._config: dict[str, Any] = loaded

    @property
    def system_prompt(self) -> str:
        """系统提示词."""
        value = self._config.get("system_prompt", "")
        if not isinstance(value, str):
            raise ValueError("'system_prompt' must be a string")
        return value

    @property
    def llm_config(self) -> dict[str, Any]:
        """LLM 配置覆盖（可选）."""
        value = self._config.get("llm", {})
        if not isinstance(value, Mapping):
            raise ValueError("'llm' must be a mapping")
        return dict(value)

    @property
    def capabilities(self) -> list[str]:
        """启用的能力列表.

        Example:
            capabilities:
              - basic_tools
              - knowledge
              - memory
        """
        value = self._config.get("capabilities", [])
        if isinstance(value, Mapping):
            if not all(
                isinstance(key, str) and key.strip() and isinstance(config, Mapping)
                for key, config in value.items()
            ):
                raise ValueError("'capabilities' mapping requires names and configuration mappings")
            return list(value)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError("'capabilities' must be a list of strings or a mapping")
        return value

    @property
    def tools(self) -> list[str]:
        """启用的工具列表（可选，用于细粒度控制）.

        Example:
            tools:
              - get_current_time
              - query_tickets
        """
        value = self._config.get("tools", [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError("'tools' must be a list of strings")
        return value

    @property
    def capability_config(self) -> dict[str, Any]:
        """能力专用配置（可选）.

        Example:
            capability_config:
              knowledge:
                vector_store_url: http://localhost:6333
                collection_name: helpdesk_docs
        """
        value = self._config.get("capability_config", {})
        if not isinstance(value, Mapping):
            raise ValueError("'capability_config' must be a mapping")
        result = dict(value)
        declared = self._config.get("capabilities", [])
        if isinstance(declared, Mapping):
            # Validate both views before allowing any plugin to initialize.
            names = self.capabilities
            for name in names:
                config = declared[name]
                if name in result:
                    raise ValueError(f"Duplicate configuration for capability '{name}'")
                result[name] = dict(config)
        return result


def load_llm_config() -> LLMConfig:
    """加载 LLM 配置（从环境变量）.

    Returns:
        LLMConfig: LLM 配置

    Raises:
        ValidationError: 缺少必需的环境变量
    """
    return LLMConfig()  # type: ignore


def load_agent_config(config_path: Path | str) -> AgentConfig:
    """加载 Agent 配置（从 YAML）.

    Args:
        config_path: agent.yml 路径

    Returns:
        AgentConfig: Agent 配置

    Raises:
        FileNotFoundError: 配置文件不存在
    """
    return AgentConfig(config_path)

"""Project-owned read-only example specialist using the existing local handle."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from capabilities.multiagent.handle import AgentHandle, LocalAgentHandle
from core.config_loader import AgentConfig, load_llm_config
from core.kernel.engine import AgentEngine


class ExpertConfig(BaseModel):
    """Trusted deployment configuration, never model-supplied routing parameters."""

    model_config = ConfigDict(extra="forbid")
    name: str = "ticket_analyst"
    description: str = "工单分析专家：只读查询工单、操作历史和相似工单；使用独立模拟数据快照。"
    config_path: Path = Field(default=Path("projects/helpdesk/config/agent_ticket_expert.yml"))


def get_agent(config: dict[str, Any]) -> AgentHandle:
    """Create a fresh child engine per delegation and always close its resources."""
    if config.get("name") == "knowledge_expert":
        config = {
            "description": "知识库专家：只读检索支持文档、工单与历史；业务为独立模拟快照，文档为示例设定。",
            "config_path": "projects/helpdesk/config/agent_knowledge_expert.yml",
            **config,
        }
    settings = ExpertConfig.model_validate(config)

    @asynccontextmanager
    async def engine_scope() -> AsyncIterator[AgentEngine]:
        engine = await AgentEngine.create(load_llm_config(), AgentConfig(settings.config_path))
        try:
            yield engine
        finally:
            await engine.close()

    return LocalAgentHandle(settings.name, settings.description, engine_scope)

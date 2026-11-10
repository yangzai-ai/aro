"""Optional catalogue/tool assembly using trusted project agent factories."""

import importlib
from contextvars import ContextVar
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from capabilities.multiagent.registry import AgentRegistry
from capabilities.multiagent.supervisor import (
    DelegateAgentTool,
    DelegationHook,
    Supervisor,
    TurnScope,
)
from core.kernel.capability import Capability
from core.kernel.hook import Hook
from core.tools.base import BaseTool


class AgentFactoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    module: str = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)


class MultiAgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agents: list[AgentFactoryConfig] = Field(min_length=1, max_length=8)
    timeout_seconds: float = Field(default=180, gt=0, le=600)


class MultiAgentCapability(Capability):
    def __init__(self, config: dict[str, Any], *, registry: AgentRegistry | None = None) -> None:
        if registry is None:
            settings = MultiAgentConfig.model_validate(config)
            registry = AgentRegistry()
            for agent in settings.agents:
                factory = importlib.import_module(agent.module).get_agent
                registry.register(factory(agent.config))
            timeout = settings.timeout_seconds
        else:
            if not registry.catalogue():
                raise ValueError("At least one specialist is required")
            timeout = float(config.get("timeout_seconds", 180))
            if not 0 < timeout <= 600:
                raise ValueError("Invalid delegation timeout")
        self.registry = registry
        self.supervisor = Supervisor(registry)
        scope: ContextVar[TurnScope | None] = ContextVar("delegation_scope", default=None)
        self.delegate_tool = DelegateAgentTool(self.supervisor, scope, timeout)
        self.hook = DelegationHook(scope)

    @property
    def name(self) -> str:
        return "multiagent"

    def tools(self) -> list[BaseTool]:
        return [self.delegate_tool]

    def hooks(self) -> list[Hook]:
        return [self.hook]


def get_capability(config: dict[str, Any]) -> Capability:
    return MultiAgentCapability(config)

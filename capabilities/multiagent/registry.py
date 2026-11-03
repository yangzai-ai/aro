"""Agent metadata catalogue; no routing policy or local implementation checks."""

import re

from pydantic import BaseModel, ConfigDict

from capabilities.multiagent.handle import AgentHandle


class AgentDescription(BaseModel):
    """The entire catalogue visible to the supervisor model."""

    model_config = ConfigDict(frozen=True)
    name: str
    description: str


class AgentRegistry:
    """Register once; resolve by name through the uniform invocation interface."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentHandle] = {}

    def register(self, handle: AgentHandle) -> None:
        if not isinstance(handle, AgentHandle):
            raise TypeError("Agent must implement AgentHandle")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", handle.name) or not handle.description.strip():
            raise ValueError("Agent requires a stable identifier and nonempty description")
        if handle.name in self._agents:
            raise ValueError(f"Duplicate agent: {handle.name}")
        self._agents[handle.name] = handle

    def get(self, name: str) -> AgentHandle:
        if name not in self._agents:
            raise KeyError(f"Unknown agent: {name}")
        return self._agents[name]

    def catalogue(self) -> list[AgentDescription]:
        return [
            AgentDescription(name=agent.name, description=agent.description)
            for agent in self._agents.values()
        ]

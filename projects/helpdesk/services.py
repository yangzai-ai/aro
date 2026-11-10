"""Project-owned adapters from concrete capabilities to neutral host services."""

from collections.abc import Sequence

from core.config_loader import AgentConfig
from core.kernel.services import ExpertDirectory, ExpertInfo, LocalExpert


class HelpdeskExperts:
    """Read catalogue metadata without starting any engine or loading models."""

    def __init__(self, config: AgentConfig) -> None:
        self._handles: dict[str, LocalExpert] = {}
        self._catalogue: Sequence[ExpertInfo] = []
        if "multiagent" not in config.capabilities:
            return
        from capabilities.multiagent.capability import MultiAgentCapability
        from capabilities.multiagent.handle import LocalAgentHandle

        capability = MultiAgentCapability(config.capability_config["multiagent"])
        self._catalogue = capability.registry.catalogue()
        for item in self._catalogue:
            handle = capability.registry.get(item.name)
            if not isinstance(handle, LocalAgentHandle):
                raise ValueError("Direct sessions require a local expert engine scope")
            self._handles[item.name] = handle

    def catalogue(self) -> Sequence[ExpertInfo]:
        return self._catalogue

    def get(self, name: str) -> LocalExpert:
        return self._handles[name]


def expert_directory(config: AgentConfig) -> ExpertDirectory:
    return HelpdeskExperts(config)

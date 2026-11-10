"""Capability assembly for human approval hook."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from core.kernel.capability import Capability
from core.kernel.hook import Hook
from core.tools.base import BaseTool

from .approval_manager import ApprovalManager
from .hook import HITLHook


class HITLConfig(BaseModel):
    """Only CLI approval is supported by this slice."""

    model_config = ConfigDict(extra="forbid")
    mode: Literal["cli", "api"] = "cli"
    timeout_seconds: float = Field(default=300, gt=0, le=600)


class HITLCapability(Capability):
    """Expose HITL as a hook and no tools."""

    def __init__(self, config: dict[str, Any], tool_map: dict[str, BaseTool] | None = None) -> None:
        settings = HITLConfig.model_validate(config)
        self.approval_manager = ApprovalManager(settings.mode, settings.timeout_seconds)
        self.hook = HITLHook(self.approval_manager, tool_map)
        # Assembly metadata only; HITLHook's approval behavior is unchanged.
        self.hook.provides = {"approvals": self.approval_manager}

    @property
    def name(self) -> str:
        return "hitl"

    def tools(self) -> list[BaseTool]:
        return []

    def hooks(self) -> list[Hook]:
        return [self.hook]


def get_capability(config: dict[str, Any]) -> Capability:
    """Standard plugin factory; tools are bound through the hook contract."""
    return HITLCapability(config)

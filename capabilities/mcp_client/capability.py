"""Optional configured discovery, conservative permissions, isolated failures."""

import asyncio
import logging
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from core.kernel.capability import Capability
from core.kernel.middleware import Middleware
from core.tools.base import BaseTool, PermissionLevel

from .client import ServerConfig, discover
from .tool_adapter import MCPTool

logger = logging.getLogger(__name__)


class MCPConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    servers: dict[str, ServerConfig] = Field(min_length=1, max_length=8)


class MCPClientCapability(Capability):
    def __init__(self, config: dict[str, Any]) -> None:
        self.settings = MCPConfig.model_validate(config)
        for name, settings in self.settings.servers.items():
            if not re.fullmatch(r"[a-z][a-z0-9]{0,15}", name):
                raise ValueError(
                    "Server identifiers: lowercase letters/digits, start with letter, max16"
                )
            settings.connection()
            if any(p not in {"read", "write", "forbidden"} for p in settings.permissions.values()):
                raise ValueError("Permissions must be read/write/forbidden")
        self._tools: list[MCPTool] = []
        self._started = False

    @property
    def name(self) -> str:
        return "mcp_client"

    async def start(self) -> None:
        if self._started:
            raise RuntimeError("MCP capability already started")
        for name, settings in self.settings.servers.items():
            try:
                async with asyncio.timeout(settings.discovery_timeout_seconds):
                    remote_tools = await discover(name, settings)
                wrapped = []
                for remote in remote_tools:
                    permission: PermissionLevel = "write"
                    configured = settings.permissions.get(remote.name)
                    if configured == "read":
                        permission = "read"
                    elif configured == "forbidden":
                        permission = "forbidden"
                    wrapped.append(MCPTool(name, remote, permission, settings.call_timeout_seconds))
                if len({tool.name for tool in wrapped}) != len(wrapped):
                    raise ValueError("Duplicate MCP tool names")
                unknown = settings.permissions.keys() - {tool.name for tool in remote_tools}
                if unknown:
                    logger.warning("MCP server %s has unknown permission overrides", name)
                self._tools.extend(wrapped)
            except Exception as exc:
                logger.warning("MCP server %s unavailable (%s)", name, type(exc).__name__)
        self._started = True

    async def close(self) -> None:
        for tool in self._tools:
            tool.closed = True
        self._tools.clear()

    def tools(self) -> list[BaseTool]:
        if not self._started:
            raise RuntimeError("MCP discovery requires await AgentEngine.create(...)")
        return list(self._tools)

    def middlewares(self) -> list[Middleware]:
        return []


def get_capability(config: dict[str, Any]) -> Capability:
    return MCPClientCapability(config)

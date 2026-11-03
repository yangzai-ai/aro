"""Bounded discovery using the official adapter's stateless sessions."""

from typing import Any, cast

from langchain_core.tools import BaseTool as LangChainTool
from pydantic import BaseModel, ConfigDict, Field


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transport: str = "stdio"
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None
    permissions: dict[str, str] = Field(default_factory=dict)
    discovery_timeout_seconds: float = Field(default=15, gt=0, le=120)
    call_timeout_seconds: float = Field(default=30, gt=0, le=600)

    def connection(self) -> dict[str, Any]:
        if self.transport != "stdio":
            raise ValueError("v0.8 supports stdio transport only")
        return self.model_dump(
            include={"transport", "command", "args", "env", "cwd"}, exclude_none=True
        )


async def discover(server: str, settings: ServerConfig) -> list[LangChainTool]:
    # This import happens only when the configured capability starts.
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from langchain_mcp_adapters.sessions import StdioConnection

    client = MultiServerMCPClient({server: cast(StdioConnection, settings.connection())})
    # Select one server explicitly: adapter owns and closes its discovery session.
    # Returned tools open/close their own session for each invocation.
    return await client.get_tools(server_name=server)

"""Standalone optional template capability; project facts are not interpreted here."""

from typing import Any

from pydantic import BaseModel

from capabilities.doc_generation.public import DocumentConfig, DocumentService, RenderRequest
from core.kernel.capability import Capability
from core.kernel.middleware import Middleware
from core.tools.base import BaseTool, PermissionLevel, ToolResult


class RenderTool(BaseTool):
    def __init__(self, service: DocumentService) -> None:
        self.service = service

    @property
    def name(self) -> str:
        return "render_word_template"

    @property
    def description(self) -> str:
        return "Render a trusted server-configured Word template"

    @property
    def usage_prompt(self) -> str:
        return "Only explicit document requests. Requires human authorization; no paths or template code."

    @property
    def permission(self) -> PermissionLevel:
        return "write"

    @property
    def schema(self) -> type[BaseModel]:
        return RenderRequest

    async def run(self, **kwargs: Any) -> ToolResult:
        try:
            item = await self.service.render(RenderRequest.model_validate(kwargs))
            return ToolResult(True, {"artifact": item.model_dump(mode="json")})
        except Exception:
            return ToolResult(False, error="Word generation failed; check template and data")


class DocumentCapability(Capability):
    def __init__(self, config: dict[str, Any]) -> None:
        self.service = DocumentService(DocumentConfig.model_validate(config))

    @property
    def name(self) -> str:
        return "doc_generation"

    async def start(self) -> None:
        await self.service.start()

    async def close(self) -> None:
        await self.service.close()

    def tools(self) -> list[BaseTool]:
        return [RenderTool(self.service)]

    def middlewares(self) -> list[Middleware]:
        return []


def get_capability(config: dict[str, Any]) -> Capability:
    return DocumentCapability(config)

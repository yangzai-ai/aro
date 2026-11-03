"""Helpdesk workbench: evidence tool wrappers + Word report generation (v1.0)."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from core.kernel.capability import Capability
from core.kernel.middleware import Middleware
from core.tools.base import BaseTool, PermissionLevel, ToolResult
from projects.helpdesk.capability import HelpdeskCapability, HelpdeskConfig, create_repository

if TYPE_CHECKING:
    from capabilities.doc_generation.public import Artifact, ArtifactContent, DocumentService


async def wait_for_cleanup(task: asyncio.Task[None], timeout: float = 2.0) -> None:
    try:
        await asyncio.wait_for(task, timeout)
    except TimeoutError:
        logging.getLogger(__name__).warning("Cleanup timeout; cancelling")
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


class WorkbenchConfig(HelpdeskConfig):
    module: str = Field(default="projects.helpdesk.workbench")
    reports: dict[str, Any] | None = None


class EvidenceTool(BaseTool):
    def __init__(self, inner: BaseTool, owner: Workbench) -> None:
        self.inner = inner
        self.owner = owner

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def description(self) -> str:
        return self.inner.description

    @property
    def usage_prompt(self) -> str:
        return self.inner.usage_prompt

    @property
    def permission(self) -> PermissionLevel:
        return self.inner.permission

    @property
    def schema(self) -> type[BaseModel] | dict[str, Any]:
        return self.inner.schema

    async def run(self, **kwargs: Any) -> ToolResult:
        result = await self.inner.run(**kwargs)
        if result.success and self.name == "update_ticket":
            self.owner.updates.append({
                "observed_at": datetime.now(UTC).isoformat(),
                "result": result.output,
            })
        elif result.success and self.name == "check_duplicate_tickets":
            query = result.output["query"]
            self.owner.duplicates[(query["title"], query["category"])] = {
                "observed_at": datetime.now(UTC).isoformat(),
                "result": result.output,
            }
        return result


class ReportParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str = Field(description="工单编号")


class ReportTool(BaseTool):
    def __init__(self, owner: Workbench) -> None:
        self.owner = owner

    @property
    def name(self) -> str:
        return "generate_ticket_report"

    @property
    def description(self) -> str:
        return "为指定工单生成 Word 状态报告，需人工确认"

    @property
    def usage_prompt(self) -> str:
        return (
            "用户明确要求生成/下载 Word 报告时调用。仅提供已知 ticket_id。"
            "事实由服务端执行时读取，不接受自填状态或路径。通过运行时确认后生成，"
            "人工取消后不重试。报告不是工单处理依据，下载由界面产物卡片提供，不编造链接。"
        )

    @property
    def permission(self) -> PermissionLevel:
        return "write"

    @property
    def schema(self) -> type[BaseModel]:
        return ReportParameters

    async def run(self, **kwargs: Any) -> ToolResult:
        try:
            request = ReportParameters.model_validate(kwargs)
            artifact = await self.owner.generate_report(request.ticket_id)
            return ToolResult(
                True,
                {
                    "artifact": artifact.model_dump(mode="json"),
                    "ticket_id": request.ticket_id,
                },
            )
        except Exception as exc:
            logging.getLogger(__name__).warning("Report failed (%s)", type(exc).__name__)
            return ToolResult(False, error="报告未生成，请检查工单、模板或数据源。")


class Workbench(Capability):
    def __init__(self, settings: WorkbenchConfig) -> None:
        self.settings = settings
        self.repository = create_repository(settings)
        self.duplicates: dict[tuple[str, str], dict[str, Any]] = {}
        self.updates: list[dict[str, Any]] = []
        self.documents: DocumentService | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._tools: list[BaseTool] = [
            EvidenceTool(t, self) for t in HelpdeskCapability(self.repository).tools()
        ]

    @property
    def name(self) -> str:
        return "helpdesk_tools"

    async def start(self) -> None:
        if self.settings.reports is not None:
            from capabilities.doc_generation.public import DocumentConfig, DocumentService

            self.documents = DocumentService(DocumentConfig.model_validate(self.settings.reports))
            try:
                await self.documents.start()
                self._tools.append(ReportTool(self))
            except BaseException:
                await self.close()
                raise

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._cleanup())
        await wait_for_cleanup(self._close_task)

    async def _cleanup(self) -> None:
        if self.documents is not None:
            await self.documents.close()

    def tools(self) -> list[BaseTool]:
        return list(self._tools)

    def middlewares(self) -> list[Middleware]:
        return []

    async def artifacts(self) -> list[Artifact]:
        return await self.documents.list_artifacts() if self.documents else []

    async def read_artifact(self, artifact_id: str) -> ArtifactContent:
        if self.documents is None:
            raise ValueError("Reports disabled")
        return await self.documents.read(artifact_id)

    async def panel(self, selected: str | None = None) -> dict[str, Any]:
        rows = await self.repository.query_tickets()
        detail = await self.repository.get_ticket(selected) if selected else None
        history = None
        if detail:
            try:
                history = [
                    h.model_dump(mode="json")
                    for h in await self.repository.get_ticket_history(detail.id, limit=5)
                ]
            except Exception:
                history = None
        return {
            "source": self.settings.data_source,
            "observed_at": datetime.now(UTC).isoformat(),
            "tickets": [t.model_dump(mode="json") for t in rows],
            "counts": {
                s: sum(t.status.value == s for t in rows)
                for s in ["open", "assigned", "in_progress", "resolved", "closed"]
            },
            "selected": detail.model_dump(mode="json") if detail else None,
            "history": history,
            "duplicate_check": (
                self.duplicates.get((detail.title, detail.category.value)) if detail else None
            ),
            "artifacts": [a.model_dump(mode="json") for a in await self.artifacts()],
        }

    async def generate_report(self, ticket_id: str) -> Artifact:
        from capabilities.doc_generation.public import RenderRequest

        if self.documents is None:
            raise ValueError("Reports disabled")
        ticket = await self.repository.get_ticket(ticket_id)
        if ticket is None:
            raise ValueError("Unknown ticket")
        now = datetime.now(UTC).isoformat()
        update_records = [
            f"观察时间：{item['observed_at']}；工单：{item['result'].get('ticket_id')}；"
            f"操作：{item['result'].get('action', '不可用')}；"
            f"操作人：{item['result'].get('operator') or '未提供'}"
            for item in self.updates
            if item["result"].get("ticket_id") == ticket_id
        ]
        duplicate = self.duplicates.get((ticket.title, ticket.category.value))
        duplicate_text = "未检查：本会话无成功检查记录。"
        if duplicate:
            dup_list = duplicate["result"].get("duplicates", [])
            duplicate_text = (
                f"检查记录时间：{duplicate['observed_at']}\n"
                f"重复工单：{', '.join(t['id'] for t in dup_list) if dup_list else '未发现重复'}"
            )
        data = {
            "ticket_id": ticket.id,
            "title": ticket.title,
            "description": ticket.description,
            "category": ticket.category.value,
            "priority": ticket.priority.value,
            "status": ticket.status.value,
            "requester": ticket.requester,
            "assignee": ticket.assignee or "未分配",
            "created_at": ticket.created_at.isoformat(),
            "updated_at": ticket.updated_at.isoformat(),
            "resolved_at": ticket.resolved_at.isoformat() if ticket.resolved_at else "未解决",
            "update_history": "\n".join(update_records) or "本会话未观察到更新操作。",
            "duplicate_check": duplicate_text,
            "source": f"{self.settings.data_source} / HelpdeskRepository 公开接口",
            "generated_at": now,
            "limitation": (
                "模拟数据。" if self.settings.data_source == "mock" else "业务数据源。"
            ) + "仅演示框架能力，不构成实际工单处理依据。",
        }
        return await self.documents.render(RenderRequest(template_id="ticket", data=data))


def get_capability(config: dict[str, Any]) -> Capability:
    return Workbench(WorkbenchConfig.model_validate(config))

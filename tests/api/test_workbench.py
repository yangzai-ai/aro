"""Five-scene project composition, facts and authorized artifact transport."""

import asyncio
import io
import json
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml
from docx import Document
from langchain_core.messages import ToolMessage

from api.settings import APISettings
from capabilities.doc_generation.public import DocumentConfig, DocumentService, RenderRequest
from capabilities.knowledge.embedding.bge_m3 import BGEM3Embedding
from capabilities.knowledge.indexer import index_documents
from capabilities.knowledge.vectordb.qdrant import QdrantVectorDB
from core.config_loader import LLMConfig
from core.kernel.engine import AgentEngine
from projects.helpdesk.api import create_app
from projects.helpdesk.workbench import Workbench, WorkbenchConfig
from tests.api.helpers import OpenChat
from tests.test_kernel_loop import FakeLLMProvider


class ScenarioProvider(FakeLLMProvider):
    def __init__(self):
        super().__init__("")
        self._client = SimpleNamespace(close=AsyncMock())

    async def chat_stream(self, messages, tools=None, **kwargs):
        if isinstance(messages[-1], ToolMessage):
            yield {"delta": str(messages[-1].content)}
            yield {"finish_reason": "stop"}
            return
        question = str(messages[-1].content)
        name, args = {
            "query": ("query_tickets", {"status": "open"}),
            "knowledge": ("search_knowledge_base", {"query": "打印机", "top_k": 1}),
            "duplicate_check": ("check_duplicate_tickets", {"title": "无法登录邮箱系统", "category": "account"}),
            "approve": (
                "update_ticket",
                {"ticket_id": "TKT-001", "status": "resolved", "assignee": "支持组 & <safe>"},
            ),
            "reject": ("update_ticket", {"ticket_id": "TKT-002", "status": "closed"}),
            "report": ("generate_ticket_report", {"ticket_id": "TKT-001"}),
            "unknown": ("generate_ticket_report", {"ticket_id": "not-known"}),
        }[question]
        yield {
            "tool_calls_delta": [
                {
                    "index": 0,
                    "id": "scenario1",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args),
                    },
                }
            ]
        }
        yield {"finish_reason": "tool_calls"}


@pytest.fixture
async def workbench_app(tmp_path, monkeypatch):
    class Embedding:
        def __init__(self, *args, **kwargs):
            pass

        def encode(self, texts, **kwargs):
            return {
                "dense_vecs": SimpleNamespace(tolist=lambda: [[1.0] + [0.0] * 1023 for _ in texts])
            }

    class Reranker:
        def __init__(self, *args, **kwargs):
            pass

        def compute_score(self, pairs, **kwargs):
            return [1.0 for _ in pairs]

    monkeypatch.setitem(
        sys.modules,
        "FlagEmbedding",
        SimpleNamespace(BGEM3FlagModel=Embedding, FlagReranker=Reranker),
    )
    monkeypatch.setattr("capabilities.knowledge.embedding.bge_m3.resolve_model_path", lambda n: n)
    monkeypatch.setattr(
        "capabilities.knowledge.reranker.bge_reranker.resolve_model_path", lambda n: n
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "height.md").write_text("# 高度\n高度限制为演示 E05，非现行法规。", encoding="utf-8")
    database = QdrantVectorDB(str(tmp_path / "db"))
    await index_documents(docs, "docs", BGEM3Embedding(device="cpu"), database)
    await database.close()
    config = yaml.safe_load(
        Path("projects/helpdesk/config/agent_workbench_light.yml").read_text(encoding="utf-8")
    )
    config["capability_config"]["helpdesk_tools"]["reports"]["output_dir"] = str(
        tmp_path / "reports"
    )
    config["capabilities"].append("knowledge")
    config["tools"].append("search_knowledge_base")
    config["capability_config"]["knowledge"] = {
        "collection_name": "docs",
        "qdrant_path": str(tmp_path / "db"),
        "device": "cpu",
        "embedding_model": "v10-test-embedding",
        "reranker_model": "v10-test-reranker",
        "feedback_file": str(tmp_path / "feedback.jsonl"),
    }
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr("api.sessions.load_llm_config", lambda: LLMConfig(OPENAI_API_KEY="fake"))
    monkeypatch.setattr(AgentEngine, "_init_llm", lambda self: ScenarioProvider())
    app = create_app(APISettings(_env_file=None, config=path, token="demo-token"))
    async with app.router.lifespan_context(app):
        yield app


class WorkbenchChat(OpenChat):
    async def next(self, expected=None):
        event = await asyncio.wait_for(self.events.get(), timeout=20)
        if expected is not None:
            assert event["type"] == expected, event
        return event


async def turn(app, question, session=None, decision=None):
    async with WorkbenchChat(app, question, session, token="demo-token") as chat:
        if decision is not None:
            event = (await chat.until("approval_required"))[-1]
            identity = session or next(iter(app.state.sessions.sessions))
            app.state.sessions.sessions[identity].approvals.decide(
                event["data"]["approval_id"], identity, decision
            )
        events = await chat.until("done")
        return events[-1]["data"]


async def test_scenes_consistency_ownership_and_report_download(workbench_app):
    app = workbench_app
    first = await turn(app, "query")
    one = first["session_id"]
    assert first["trace"]["tool_calls"][0]["result"]["count"] == 2
    two = (await turn(app, "query"))["session_id"]
    headers = {"authorization": "Bearer demo-token", "x-aro-session": one}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        panel = (await client.get("/api/helpdesk/panel?selected=TKT-001", headers=headers)).json()
        assert panel["counts"]["open"] == 2 and panel["selected"]["id"] == "TKT-001"
        assert panel["history"] == []
        await turn(app, "duplicate_check", one)
        knowledge = await turn(app, "knowledge", one)
        evidence = knowledge["trace"]["tool_calls"][0]
        assert evidence["success"] and evidence["result"]["results"][0]["source"] == "height.md"
        await turn(app, "approve", one, False)
        assert (await client.get("/api/helpdesk/panel", headers=headers)).json()["counts"][
            "open"
        ] == 2
        await turn(app, "approve", one, True)
        await turn(app, "reject", one, True)
        panel = (await client.get("/api/helpdesk/panel?selected=TKT-001", headers=headers)).json()
        assert panel["counts"] == {"open": 1, "assigned": 0, "in_progress": 1, "resolved": 2, "closed": 1}
        assert panel["duplicate_check"] is not None, "Successful duplicate checks must remain observable"
        assert [t["id"] for t in panel["duplicate_check"]["result"]["duplicates"]] == ["TKT-001"]
        report = await turn(app, "report", one, True)
        artifact = report["trace"]["tool_calls"][0]["result"]["artifact"]
        url = f"/api/helpdesk/artifacts/{artifact['id']}"
        for bad_headers in [{}, {**headers, "authorization": "Bearer wrong"}]:
            assert (await client.get(url, headers=bad_headers)).status_code == 401
        assert (await client.get(url, headers={**headers, "x-aro-session": two})).status_code == 404
        response = await client.get(url, headers=headers)
        assert (
            response.status_code == 200 and "attachment" in response.headers["content-disposition"]
        )
        doc = Document(io.BytesIO(response.content))
        text = "\n".join(p.text for p in doc.paragraphs) + "\n".join(
            c.text for t in doc.tables for r in t.rows for c in r.cells
        )
        for expected in [
            "resolved",
            "TKT-001",
            "支持组 & <safe>",
            "无法登录邮箱系统",
            "HelpdeskRepository",
            "仅演示框架能力",
        ]:
            assert expected in text
        assert "{{" not in text and one not in text
        assert (
            await client.get("/api/helpdesk/panel", headers={**headers, "x-aro-session": two})
        ).json()["counts"]["open"] == 2
        failed = await turn(app, "unknown", one, True)
        assert not failed["trace"]["tool_calls"][0]["success"]
        cap = app.state.sessions.sessions[one].engine.capabilities[0]
        assert len(await cap.artifacts()) == 1
        assert (await client.delete("/api/helpdesk/session", headers=headers)).status_code == 200
        assert not await cap.artifacts()
        assert (await client.get(url, headers=headers)).status_code == 404


async def test_close_during_pending_approval_invalidates_before_cleanup(workbench_app):
    app = workbench_app
    one = (await turn(app, "query"))["session_id"]
    session = app.state.sessions.sessions[one]
    async with OpenChat(app, "report", one, token="demo-token") as chat:
        await chat.until("approval_required")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            headers = {"authorization": "Bearer demo-token", "x-aro-session": one}
            assert (
                await client.delete("/api/helpdesk/session", headers=headers)
            ).status_code == 200
            assert (await client.get("/api/helpdesk/panel", headers=headers)).status_code == 404
    assert session.task.done() and not session.approvals.pending
    assert not await session.engine.capabilities[0].artifacts()


async def test_render_cancel_publication_read_expiry_and_cleanup(tmp_path, monkeypatch):
    from docxtpl import DocxTemplate

    config = DocumentConfig(
        templates={"ticket": "projects/helpdesk/templates/ticket.docx"},
        output_dir=tmp_path,
    )
    service = DocumentService(config)
    await service.start()
    entered, finish = threading.Event(), threading.Event()
    original = DocxTemplate.save

    def held_save(self, *args, **kwargs):
        entered.set()
        assert finish.wait(5)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(DocxTemplate, "save", held_save)
    data = dict.fromkeys(
        [
            "ticket_id",
            "title",
            "description",
            "category",
            "priority",
            "requester",
            "assignee",
            "created_at",
            "updated_at",
            "resolved_at",
            "status",
            "update_history",
            "duplicate_check",
            "source",
            "generated_at",
            "limitation",
        ],
        "未提供",
    )
    task = asyncio.create_task(service.render(RenderRequest(template_id="ticket", data=data)))
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    listing = asyncio.create_task(service.list_artifacts())
    await asyncio.sleep(0)
    assert not task.done() and not listing.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    items = await listing
    assert len(items) == 1 and (await service.read(items[0].id)).content.startswith(b"PK")
    with pytest.raises(ValueError):
        await service.read("../../ticket.docx")
    with pytest.raises(ValueError):
        await service.render(RenderRequest(template_id="evil", data=data))
    # Deterministic clock replacement, no timing assumption.
    import capabilities.doc_generation.public as public

    class FutureDateTime:
        @staticmethod
        def now(tz):
            return datetime.now(UTC) + timedelta(days=2)

    monkeypatch.setattr(public, "datetime", FutureDateTime)
    with pytest.raises(ValueError):
        await service.read(items[0].id)
    await asyncio.gather(service.close(), service.close())
    assert not list(tmp_path.iterdir())






def test_disabled_project_imports_in_fresh_interpreter(tmp_path):
    config = tmp_path / "disabled.yml"
    config.write_text(
        "capabilities: [helpdesk_tools]\ntools: [query_tickets]\ncapability_config:\n  helpdesk_tools:\n    module: projects.helpdesk.workbench\n",
        encoding="utf-8",
    )
    code = """
import asyncio,sys
from unittest.mock import patch
from projects.helpdesk.api import create_app
from api.settings import APISettings
from core.config_loader import AgentConfig, LLMConfig
from core.kernel.engine import AgentEngine
async def main():
 settings=LLMConfig(OPENAI_API_KEY='fake',OPENAI_API_BASE='http://unused.test')
 engine=await AgentEngine.create(settings, AgentConfig(sys.argv[1]))
 await engine.close()
 app=create_app(APISettings(_env_file=None, config=sys.argv[1]))
 with patch('api.sessions.load_llm_config', return_value=settings):
  async with app.router.lifespan_context(app):
   session=await app.state.sessions.reserve(None)
   session.busy=False
asyncio.run(main())
assert not any(x in sys.modules for x in ['capabilities.doc_generation','docxtpl','docx','jinja2','capabilities.knowledge','torch','capabilities.mcp_client']), sorted(sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(config)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


async def test_http_repository_report_reads_only_public_facts(tmp_path, monkeypatch):
    from projects.helpdesk.repositories.http import HttpRepository
    from projects.helpdesk.repositories.mock import MockRepository

    # A public wire payload, then a real HttpRepository parses it; no mock fields used by project.
    payload = [d.model_dump(mode="json") for d in await MockRepository().query_tickets()]
    payload[0]["requester"] = "HTTP 请求人 & <安全> {{7*7}}"
    calls = []

    async def handle(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=payload[0])

    repo = HttpRepository("http://example.test", transport=httpx.MockTransport(handle))
    monkeypatch.setattr("projects.helpdesk.workbench.create_repository", lambda config: repo)
    cap = Workbench(
        WorkbenchConfig(
            data_source="production",
            base_url="http://example.test",
            reports={
                "templates": {"ticket": "projects/helpdesk/templates/ticket.docx"},
                "output_dir": str(tmp_path),
            },
        )
    )
    await cap.start()
    try:
        artifact = await cap.generate_report("TKT-001")
        doc = Document(io.BytesIO((await cap.read_artifact(artifact.id)).content))
        text = "\n".join(p.text for p in doc.paragraphs) + "\n".join(
            c.text for t in doc.tables for r in t.rows for c in r.cells
        )
        assert "HTTP 请求人 & <安全> {{7*7}}" in text
        assert "未检查" in text and "本会话未观察到更新操作" in text and "production" in text
        assert calls == ["/tickets/TKT-001"]
    finally:
        await cap.close()


async def test_nested_service_start_close_and_failed_start_rollback(tmp_path, monkeypatch):
    counts = {"start": 0, "close": 0}
    original_start, original_close = DocumentService.start, DocumentService.close

    async def start(self):
        counts["start"] += 1
        await original_start(self)

    async def close(self):
        counts["close"] += 1
        await original_close(self)

    monkeypatch.setattr(DocumentService, "start", start)
    monkeypatch.setattr(DocumentService, "close", close)
    for valid in [True, False]:
        cap = Workbench(
            WorkbenchConfig(
                reports={
                    "templates": {
                        "ticket": "projects/helpdesk/templates/ticket.docx"
                        if valid
                        else "missing.docx"
                    },
                    "output_dir": str(tmp_path),
                }
            )
        )
        if valid:
            await cap.start()
        else:
            with pytest.raises(ValueError):
                await cap.start()
        await asyncio.gather(cap.close(), cap.close())
        assert cap._close_task.done()
    assert counts == {"start": 2, "close": 2}
    assert not list(tmp_path.iterdir())


async def test_read_in_progress_close_waits_for_bytes_and_no_file_race(tmp_path, monkeypatch):
    service = DocumentService(
        DocumentConfig(
            templates={"x": "projects/helpdesk/templates/ticket.docx"}, output_dir=tmp_path
        )
    )
    await service.start()
    # Obtain actual required variables from the controlled template.
    from docxtpl import DocxTemplate

    data = dict.fromkeys(
        DocxTemplate(
            "projects/helpdesk/templates/ticket.docx"
        ).get_undeclared_template_variables(),
        "test",
    )
    item = await service.render(RenderRequest(template_id="x", data=data))
    entered, finish = threading.Event(), threading.Event()
    original = Path.read_bytes

    def held_read(path):
        entered.set()
        assert finish.wait(5)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", held_read)
    reading = asyncio.create_task(service.read(item.id))
    assert await asyncio.to_thread(entered.wait, 2)
    closing = asyncio.create_task(service.close())
    await asyncio.sleep(0)
    assert not closing.done()
    finish.set()
    assert (await reading).content.startswith(b"PK")
    await closing
    assert not list(tmp_path.iterdir())


async def test_report_denied_without_hitl_and_render_failure_no_artifact(tmp_path):
    from core.config_loader import AgentConfig
    from core.llm.provider import LLMResponse

    config = yaml.safe_load(
        Path("projects/helpdesk/config/agent_workbench_light.yml").read_text(encoding="utf-8")
    )
    config["capabilities"].remove("hitl")
    config["capability_config"]["helpdesk_tools"]["reports"]["output_dir"] = str(tmp_path)
    path = tmp_path / "nohitl.yml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    engine = await AgentEngine.create(
        LLMConfig(OPENAI_API_KEY="fake", OPENAI_API_BASE="http://unused.test"), AgentConfig(path)
    )

    class ReportProvider(FakeLLMProvider):
        async def chat(self, messages, **kwargs):
            if isinstance(messages[-1], ToolMessage):
                return LLMResponse(content="not executed")
            return LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "report",
                        "name": "generate_ticket_report",
                        "args": {"ticket_id": "TKT-001"},
                    }
                ],
            )

    engine.loop.llm = ReportProvider("")
    try:
        await engine.run("report")
        assert not engine.trace_middleware.get_latest_trace().tool_calls[0].success
        assert "HITL" in engine.trace_middleware.get_latest_trace().tool_calls[0].error
        assert not await engine.capabilities[0].artifacts()
        service = engine.capabilities[0].documents
        with pytest.raises(ValueError):
            await service.render(RenderRequest(template_id="ticket", data={}))
        assert not await service.list_artifacts()
    finally:
        await engine.close()




async def test_forged_report_fields_and_save_failure_do_not_publish(tmp_path, monkeypatch):
    from docxtpl import DocxTemplate

    cap = Workbench(
        WorkbenchConfig(
            reports={
                "templates": {"ticket": "projects/helpdesk/templates/ticket.docx"},
                "output_dir": str(tmp_path),
            }
        )
    )
    await cap.start()
    try:
        tool = next(t for t in cap.tools() if t.name == "generate_ticket_report")
        result = await tool.run(
            ticket_id="TKT-001",
            status="resolved",
            session_id="forged",
            output_path="C:/escape.docx",
        )
        assert not result.success and not await cap.artifacts()

        def broken_save(self, path, *args, **kwargs):
            Path(path).write_bytes(b"incomplete")
            raise OSError("test failure")

        monkeypatch.setattr(DocxTemplate, "save", broken_save)
        assert not (await tool.run(ticket_id="TKT-001")).success
        assert not await cap.artifacts() and not list(tmp_path.rglob("*.tmp"))
    finally:
        await cap.close()


async def test_expired_session_cleans_report_and_panel(workbench_app):
    import time

    app = workbench_app
    identity = (await turn(app, "report", decision=True))["session_id"]
    session = app.state.sessions.sessions[identity]
    cap = next(c for c in session.engine.capabilities if isinstance(c, Workbench))
    item = (await cap.artifacts())[0]
    session.updated = time.monotonic() - app.state.settings.session_ttl_seconds - 1
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        headers = {"authorization": "Bearer demo-token", "x-aro-session": identity}
        assert (await client.get("/api/helpdesk/panel", headers=headers)).status_code == 404
        assert (
            await client.get(f"/api/helpdesk/artifacts/{item.id}", headers=headers)
        ).status_code == 404
    assert not await cap.artifacts()


async def test_completed_approval_survives_later_llm_failure(workbench_app):
    app = workbench_app
    identity = (await turn(app, "query"))["session_id"]
    session = app.state.sessions.sessions[identity]
    original = session.engine.loop.llm.chat_stream

    async def fail_after_tool(messages, **kwargs):
        if isinstance(messages[-1], ToolMessage):
            raise RuntimeError("LLM unavailable after successful business write")
        async for event in original(messages, **kwargs):
            yield event

    session.engine.loop.llm.chat_stream = fail_after_tool
    async with WorkbenchChat(app, "approve", identity, token="demo-token") as chat:
        approval = (await chat.until("approval_required"))[-1]["data"]
        session.approvals.decide(approval["approval_id"], identity, True)
        await chat.until("error")
    cap = next(c for c in session.engine.capabilities if isinstance(c, Workbench))
    assert (await cap.panel())["counts"]["resolved"] == 2
    item = await cap.generate_report("TKT-001")
    document = Document(io.BytesIO((await cap.read_artifact(item.id)).content))
    assert "resolved" in "\n".join(
        c.text for table in document.tables for row in table.rows for c in row.cells
    )


async def test_standalone_document_capability_loads_via_standard_loader(tmp_path):
    from core.config_loader import AgentConfig

    path = tmp_path / "documents.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "capabilities": ["doc_generation"],
                "tools": ["render_word_template"],
                "capability_config": {
                    "doc_generation": {
                        "templates": {"example": "projects/helpdesk/templates/ticket.docx"},
                        "output_dir": str(tmp_path / "reports"),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    engine = await AgentEngine.create(
        LLMConfig(OPENAI_API_KEY="fake", OPENAI_API_BASE="http://unused.test"), AgentConfig(path)
    )
    try:
        tool = engine.capabilities[0].tools()[0]
        assert tool.name == "render_word_template" and tool.permission == "write"
        assert not (await tool.run(template_id="../escape", data={})).success
    finally:
        await engine.close()

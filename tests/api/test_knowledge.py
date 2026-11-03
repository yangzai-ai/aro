"""Default API factory + actual disk Qdrant, fake neural constructors, concurrent SSE."""

import asyncio
import json
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml
from langchain_core.messages import ToolMessage

from api.app import create_app
from api.settings import APISettings
from capabilities.knowledge import resources
from capabilities.knowledge.embedding.bge_m3 import BGEM3Embedding
from capabilities.knowledge.indexer import index_documents
from capabilities.knowledge.vectordb.qdrant import QdrantVectorDB
from core.config_loader import LLMConfig
from core.kernel.engine import AgentEngine
from tests.api.helpers import OpenChat
from tests.test_kernel_loop import FakeLLMProvider


class QueryProvider(FakeLLMProvider):
    def __init__(self):
        super().__init__("")
        self._client = SimpleNamespace(close=AsyncMock(), is_closed=False)

    async def chat_stream(self, messages, tools=None, **kwargs):
        if isinstance(messages[-1], ToolMessage):
            yield {"delta": str(messages[-1].content)}
            yield {"finish_reason": "stop"}
            return
        question = str(messages[-1].content)
        name = "search_knowledge_base"
        args = {"query": question, "top_k": 1}
        if question == "approve":
            name = "update_ticket"
            args = {"ticket_id": "TKT-001", "status": "resolved"}
        yield {
            "tool_calls_delta": [
                {
                    "index": 0,
                    "id": "query1",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args),
                    },
                }
            ]
        }
        yield {"finish_reason": "tool_calls"}


@pytest.fixture
async def knowledge_service(tmp_path, monkeypatch):
    counts = {"embedding": 0, "reranker": 0, "client": 0}
    entered, release = threading.Event(), threading.Event()
    starts = []

    class Embedding:
        def __init__(self, *args, **kwargs):
            counts["embedding"] += 1

        def encode(self, texts, **kwargs):
            if not release.is_set():
                entered.set()
                assert release.wait(8)
            return {
                "dense_vecs": SimpleNamespace(tolist=lambda: [[1.0] + [0.0] * 1023 for _ in texts])
            }

    class Reranker:
        def __init__(self, *args, **kwargs):
            counts["reranker"] += 1

        def compute_score(self, pairs, **kwargs):
            return [float(q in doc) for q, doc in pairs]

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
    (docs / "conflict.md").write_text(
        "# 冲突\n冲突处置先调整时间，演示规则，非现行法规。", encoding="utf-8"
    )
    release.set()
    db_path = tmp_path / "db"
    database = QdrantVectorDB(str(db_path))
    await index_documents(docs, "docs", BGEM3Embedding(device="cpu"), database)
    await database.close()
    counts["embedding"] = 0
    release.clear()
    from capabilities.knowledge.vectordb import qdrant

    original_client = qdrant.QdrantClient

    def client(*args, **kwargs):
        counts["client"] += 1
        return original_client(*args, **kwargs)

    monkeypatch.setattr(qdrant, "QdrantClient", client)
    original_encode = BGEM3Embedding._encode

    def encode(self, texts):
        starts.extend(texts)
        return original_encode(self, texts)

    monkeypatch.setattr(BGEM3Embedding, "_encode", encode)
    settings = {
        "collection_name": "docs",
        "qdrant_path": str(db_path),
        "device": "cpu",
        "embedding_model": "test-embedding",
        "reranker_model": "test-reranker",
        "feedback_file": str(tmp_path / "feedback.jsonl"),
    }
    config = tmp_path / "agent.yml"
    config.write_text(
        yaml.safe_dump(
            {
                "capabilities": ["knowledge", "helpdesk_tools", "hitl"],
                "tools": [
                    "search_knowledge_base",
                    "query_tickets",
                    "update_ticket",
                ],
                "capability_config": {
                    "knowledge": settings,
                    "hitl": {"mode": "api"},
                    "helpdesk_tools": {"module": "projects.helpdesk.capability"},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "api.sessions.load_llm_config",
        lambda: LLMConfig(OPENAI_API_KEY="fake", OPENAI_API_BASE="http://unused.test"),
    )
    monkeypatch.setattr(AgentEngine, "_init_llm", lambda self: QueryProvider())
    app = create_app(APISettings(_env_file=None, config=config, token=None))  # default factory!
    try:
        async with app.router.lifespan_context(app):
            yield SimpleNamespace(
                app=app,
                counts=counts,
                entered=entered,
                release=release,
                starts=starts,
                path=db_path,
                feedback=tmp_path / "feedback.jsonl",
            )
    finally:
        release.set()
    assert resources.live_resources() == 0


async def wait_until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


async def test_default_api_cold_overlap_sharing_sources_and_session_isolation(knowledge_service):
    s = knowledge_service
    async with OpenChat(s.app, "高度") as first, OpenChat(s.app, "冲突") as second:
        await wait_until(lambda: len(s.starts) == 2)
        assert s.entered.is_set()
        assert s.counts == {"embedding": 1, "reranker": 0, "client": 1}
        sessions = list(s.app.state.sessions.sessions.values())
        one, two = sessions
        assert one.engine is not two.engine and one.engine.llm is not two.engine.llm
        assert one.state is not two.state and one.state["messages"] is not two.state["messages"]
        assert one.id != two.id and one.approvals is not two.approvals
        assert one.engine.trace_middleware is not two.engine.trace_middleware
        k1, k2 = [next(c for c in x.engine.capabilities if c.name == "knowledge") for x in sessions]
        assert k1.embedding_model is k2.embedding_model and k1.reranker is k2.reranker
        assert k1.vectordb is k2.vectordb and k1.retriever is not k2.retriever
        repos = [
            next(c.repository for c in x.engine.capabilities if c.name == "helpdesk_tools")
            for x in sessions
        ]
        assert repos[0] is not repos[1]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=s.app), base_url="http://test"
        ) as client:
            assert (
                await client.post("/chat", json={"message": "again", "session_id": one.id})
            ).status_code == 409
        s.release.set()
        r1, r2 = await asyncio.gather(first.until("done"), second.until("done"))
        for events, source in [(r1, "height.md"), (r2, "conflict.md")]:
            step = events[-1]["data"]["trace"]["tool_calls"][0]
            assert step["success"] and step["result"]["results"][0]["source"] == source
        assert s.counts == {"embedding": 1, "reranker": 1, "client": 1}
    lines = [json.loads(line) for line in s.feedback.read_text(encoding="utf-8").splitlines()]
    assert {item["query"] for item in lines} == {"高度", "冲突"}
    store = s.app.state.sessions
    store.sessions.pop(one.id)
    await store.close_session(one)
    async with OpenChat(s.app, "高度", two.id) as stream:
        assert (await stream.until("done"))[-1]["data"]["trace"]["tool_calls"][0]["success"]
    async with OpenChat(s.app, "approve", two.id) as stream:
        event = (await stream.until("approval_required"))[-1]
        assert repos[1].tickets[0].status == "open"
        two.approvals.decide(event["data"]["approval_id"], two.id, True)
        await stream.until("done")
    assert repos[0].tickets[0].status == "open"
    assert repos[1].tickets[0].status == "resolved"
    two.updated = time.monotonic() - store.settings.session_ttl_seconds - 1
    await store.expire()
    assert not store.sessions and resources.live_resources() == 0
    reopened = QdrantVectorDB(str(s.path))
    assert len(await reopened.list_documents("docs")) == 2
    await reopened.close()


async def test_disconnect_drains_inference_before_release(knowledge_service):
    s = knowledge_service
    async with OpenChat(s.app, "高度") as stream:
        await wait_until(s.entered.is_set)
        identity = next(iter(s.app.state.sessions.sessions))
        # Start disconnect while model thread is held, then release deterministically.
        stream.disconnect.set()
        await asyncio.sleep(0.02)
        session = s.app.state.sessions.sessions[identity]
        assert session.busy
        s.release.set()
    assert not session.busy and session.task.done()
    await s.app.state.sessions.close_session(session)
    assert resources.live_resources() == 0


async def test_turn_timeout_waits_for_worker_and_returns_failure(knowledge_service, monkeypatch):
    s = knowledge_service
    # Cold Qdrant/BM25/jieba preparation is setup, outside the worker deadline.
    # Use the actual API factory and the same session/retriever as the SSE turn.
    session = await s.app.state.sessions.reserve(None)
    knowledge = next(c for c in session.engine.capabilities if c.name == "knowledge")
    await knowledge.retriever.build_bm25_index()
    session.busy = False
    assert not s.entered.is_set() and s.counts["embedding"] == 0
    s.app.state.settings.turn_timeout_seconds = 0.05
    original_wait_for = asyncio.wait_for
    timed_tasks = []

    async def worker_phase_wait_for(awaitable, timeout):
        if timeout != 0.05:
            return await original_wait_for(awaitable, timeout)
        # Arm the real API deadline only after the target worker has entered.
        # No product timeout/configuration or retrieval code is changed.
        task = asyncio.ensure_future(awaitable)
        timed_tasks.append(task)
        try:
            await wait_until(s.entered.is_set)
            return await original_wait_for(task, timeout)
        finally:
            if not task.done():
                task.cancel()
                s.release.set()
                await asyncio.gather(task, return_exceptions=True)

    monkeypatch.setattr("api.app.asyncio.wait_for", worker_phase_wait_for)
    try:
        async with OpenChat(s.app, "高度", session.id) as stream:
            await wait_until(lambda: bool(timed_tasks) and timed_tasks[0].cancelling() > 0)
            assert s.entered.is_set() and s.counts["embedding"] == 1
            assert session.busy and not timed_tasks[0].done()
            assert resources.live_resources() == 3
            assert not s.release.is_set()  # Deadline fired, worker still owns resources.
            s.release.set()
            events = await stream.until("error")
            assert events[-1]["data"]["code"] == "turn_failed"
    finally:
        s.release.set()
    assert not session.busy
    assert not any(msg.type == "human" for msg in session.state["messages"])
    await s.app.state.sessions.close_session(session)
    assert resources.live_resources() == 0

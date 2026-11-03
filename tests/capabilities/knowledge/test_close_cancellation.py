"""R1: cancel the closer itself, without abandoning its shared resources."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from capabilities.knowledge import resources
from capabilities.knowledge.capability import KnowledgeCapability
from capabilities.knowledge.vectordb.base import VectorDocument
from capabilities.knowledge.vectordb.qdrant import QdrantVectorDB
from core.config_loader import AgentConfig, LLMConfig
from core.kernel.engine import AgentEngine
from tests.capabilities.knowledge.helpers import FakeEmbedding, FakeReranker


@pytest.mark.parametrize("other_owner", [False, True])
async def test_cancelled_engine_close_drains_query_and_preserves_owners(
    tmp_path, monkeypatch, other_owner
):
    monkeypatch.setattr(
        "capabilities.knowledge.capability.BGEM3Embedding", lambda *args: FakeEmbedding()
    )
    monkeypatch.setattr(
        "capabilities.knowledge.capability.BGEReranker", lambda *args: FakeReranker()
    )
    config = AgentConfig("projects/helpdesk/config/agent_api_knowledge.yml")
    config._config["capabilities"] = ["knowledge"]
    path = str(tmp_path / "db")
    config._config["capability_config"]["knowledge"]["qdrant_path"] = path
    settings = LLMConfig(OPENAI_API_KEY="fake", OPENAI_API_BASE="http://unused.test")
    engine = await AgentEngine.create(settings, config)
    other = await AgentEngine.create(settings, config) if other_owner else None
    cap = engine.capabilities[0]
    await cap.vectordb.create_collection("helpdesk_kb", 3)
    await cap.vectordb.upsert(
        "helpdesk_kb",
        [VectorDocument(doc_id="one", content="hello", vector=[1, 0, 0])],
    )
    entered, finish, closing_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def retrieve(*args, **kwargs):
        entered.set()
        await finish.wait()
        return []

    cap.retriever.retrieve = retrieve
    original_close = cap.close

    async def observed_close():
        closing_entered.set()
        await original_close()

    monkeypatch.setattr(cap, "close", observed_close)
    llm_close = AsyncMock(wraps=engine.llm._client.close)
    monkeypatch.setattr(engine.llm._client, "close", llm_close)
    query = asyncio.create_task(cap.tools()[0].run(query="hello"))
    await entered.wait()
    closing = asyncio.create_task(engine.close())
    await closing_entered.wait()
    waiter = asyncio.create_task(engine.close())
    try:
        closing.cancel()
        await asyncio.sleep(0)
        closing.cancel()
        await asyncio.sleep(0)
        assert not closing.done() and not waiter.done()
        finish.set()
        await query
        with pytest.raises(asyncio.CancelledError):
            await closing
        await waiter
        await engine.close()
        llm_close.assert_awaited_once()
        assert engine.llm._client.is_closed()
        if other:
            assert resources.live_resources() == 3
            assert (await other.capabilities[0].tools()[0].run(query="hello")).success
            await other.close()
        assert resources.live_resources() == 0
        reopened = QdrantVectorDB(path)
        assert len(await reopened.list_documents("helpdesk_kb")) == 1
        await reopened.close()
    finally:
        finish.set()
        await asyncio.gather(query, closing, waiter, return_exceptions=True)
        await cap.close()  # Keep pre-fix failure from contaminating the test process.
        await engine.llm._client.close()
        if other:
            await other.close()


@pytest.mark.parametrize("phase", ["idle", "database"])
async def test_direct_knowledge_close_cancelled_waiter_drains_all(tmp_path, monkeypatch, phase):
    cap = KnowledgeCapability({"qdrant_path": str(tmp_path / "db")})
    await cap.start()
    await cap.vectordb.create_collection("helpdesk_kb", 3)
    entered, finish = asyncio.Event(), asyncio.Event()
    query = None
    if phase == "idle":

        async def retrieve(*args, **kwargs):
            entered.set()
            await finish.wait()
            return []

        cap.retriever.retrieve = retrieve
        query = asyncio.create_task(cap.tools()[0].run(query="held"))
        await entered.wait()
    else:
        original = cap.vectordb.close

        async def held_close():
            entered.set()
            await finish.wait()
            await original()

        monkeypatch.setattr(cap.vectordb, "close", held_close)
    closing = asyncio.create_task(cap.close())
    if phase == "database":
        await entered.wait()
    else:
        await asyncio.sleep(0)
    waiter = asyncio.create_task(cap.close())
    closing.cancel()
    await asyncio.sleep(0)
    closing.cancel()
    await asyncio.sleep(0)
    assert not closing.done() and not waiter.done()
    finish.set()
    if query:
        await query
    with pytest.raises(asyncio.CancelledError):
        await closing
    await waiter
    await cap.close()
    assert cap._close_task.done() and resources.live_resources() == 0
    reopened = QdrantVectorDB(str(tmp_path / "db"))
    assert await reopened.list_documents("helpdesk_kb") == []
    await reopened.close()


async def test_internal_knowledge_cancel_during_idle_still_drains(tmp_path):
    cap = KnowledgeCapability({"qdrant_path": str(tmp_path / "db")})
    await cap.start()
    entered, finish, idle_waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def retrieve(*args, **kwargs):
        entered.set()
        await finish.wait()
        return []

    original_wait = cap._idle.wait

    async def observed_wait():
        idle_waiting.set()
        await original_wait()

    cap._idle.wait = observed_wait
    cap.retriever.retrieve = retrieve
    query = asyncio.create_task(cap.tools()[0].run(query="held"))
    await entered.wait()
    closing = asyncio.create_task(cap.close())
    await idle_waiting.wait()
    cap._close_task.cancel()
    await asyncio.sleep(0)
    assert not closing.done()
    finish.set()
    await query
    with pytest.raises(RuntimeError, match="interrupted"):
        await closing
    assert resources.live_resources() == 0

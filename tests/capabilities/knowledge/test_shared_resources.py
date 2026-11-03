"""Knowledge-only leases: identity, final release, failure/cancel and feedback safety."""

import asyncio
import json
import threading
from unittest.mock import AsyncMock

import pytest

from capabilities.knowledge import resources
from capabilities.knowledge.capability import KnowledgeCapability, KnowledgeConfig
from capabilities.knowledge.feedback import FeedbackCollector, SearchFeedback
from capabilities.knowledge.retrieval import HybridRetriever
from capabilities.knowledge.tool import SearchKnowledgeTool
from capabilities.knowledge.vectordb.base import VectorDocument
from capabilities.knowledge.vectordb.qdrant import QdrantVectorDB
from capabilities.knowledge.workers import drain_thread
from tests.capabilities.knowledge.helpers import FakeEmbedding, FakeReranker


@pytest.fixture
def neural(monkeypatch):
    monkeypatch.setattr(
        "capabilities.knowledge.capability.BGEM3Embedding", lambda *args: FakeEmbedding()
    )
    monkeypatch.setattr(
        "capabilities.knowledge.capability.BGEReranker", lambda *args: FakeReranker()
    )


async def test_equivalent_paths_collections_and_final_release(tmp_path, neural):
    settings = {"qdrant_path": str(tmp_path / "db"), "collection_name": "one", "device": "cpu"}
    a = KnowledgeCapability(settings)
    b = KnowledgeCapability(
        {**settings, "qdrant_path": str(tmp_path / "child/../db"), "collection_name": "two"}
    )
    await a.start()
    await b.start()
    try:
        assert a.vectordb is b.vectordb and a.embedding_model is b.embedding_model
        assert a.retriever is not b.retriever
        for cap, word in [(a, "first"), (b, "second")]:
            await cap.vectordb.create_collection(cap.config.collection_name, 3)
            await cap.vectordb.upsert(
                cap.config.collection_name,
                [
                    VectorDocument(
                        doc_id=word,
                        content=word,
                        vector=[1, 0, 0],
                        metadata={"source_file": word + ".md"},
                    )
                ],
            )
        first, second = await asyncio.gather(
            a.tools()[0].run(query="first"), b.tools()[0].run(query="second")
        )
        assert first.output["results"][0]["source"] == "first.md"
        assert second.output["results"][0]["source"] == "second.md"
        await a.close()
        assert (await b.tools()[0].run(query="second")).success
        assert not (await a.tools()[0].run(query="first")).success
    finally:
        await a.close()
        await b.close()
    assert resources.live_resources() == 0
    reopened = QdrantVectorDB(str(tmp_path / "db"))
    assert len(await reopened.list_documents("two")) == 1
    await reopened.close()


async def test_model_settings_and_memory_stores_do_not_alias(neural):
    caps = [
        KnowledgeCapability({"qdrant_path": ":memory:", **extra})
        for extra in [
            {},
            {"device": "cpu"},
            {"embedding_model": "different"},
            {"batch_size": 2},
            {"max_length": 32},
        ]
    ]
    try:
        for c in caps:
            await c.start()
        assert len({id(c.embedding_model) for c in caps}) == 5
        assert len({id(c.vectordb) for c in caps}) == 5
    finally:
        for c in caps:
            await c.close()
    assert resources.live_resources() == 0


async def test_remote_identity_and_start_failure_rollback(neural, monkeypatch):
    monkeypatch.setenv("QDRANT_TEST_KEY", "one")
    one = KnowledgeCapability(
        {"qdrant_url": "http://localhost:6333", "qdrant_api_key_env": "QDRANT_TEST_KEY"}
    )
    await one.start()
    monkeypatch.setenv("QDRANT_TEST_KEY", "two")
    two = KnowledgeCapability(
        {"qdrant_url": "http://localhost:6333", "qdrant_api_key_env": "QDRANT_TEST_KEY"}
    )
    three = KnowledgeCapability({"qdrant_url": "http://localhost:6334"})
    await two.start()
    await three.start()
    assert one.vectordb is not two.vectordb and two.vectordb is not three.vectordb
    assert one.embedding_model is two.embedding_model
    before = resources.live_resources()
    monkeypatch.delenv("QDRANT_TEST_KEY")
    bad = KnowledgeCapability(
        {"qdrant_url": "http://localhost:6333", "qdrant_api_key_env": "QDRANT_TEST_KEY"}
    )
    with pytest.raises(ValueError, match="environment"):
        await bad.start()
    assert resources.live_resources() == before
    for cap in [bad, one, two, three]:
        await cap.close()
    assert resources.live_resources() == 0


@pytest.mark.parametrize(
    "extra",
    [
        {"qdrant_path": "db", "qdrant_url": "http://localhost:6333"},
        {"qdrant_url": "http://user:secret@localhost:6333"},
        {"qdrant_url": "file:///local"},
        {"qdrant_path": ""},
        {"qdrant_api_key_env": "KEY"},
        {"qdrant_timeout_seconds": 0},
    ],
)
def test_storage_config_conflicts(extra):
    with pytest.raises(ValueError):
        KnowledgeConfig.model_validate(extra)


async def test_drain_thread_waits_under_repeated_cancel():
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()

    def work():
        entered.set()
        release.wait(5)
        exited.set()

    task = asyncio.create_task(drain_thread(work))
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert exited.is_set()


async def test_bm25_initial_build_once_for_overlapping_queries():
    db = QdrantVectorDB()
    await db.create_collection("docs", 3)
    await db.upsert("docs", [VectorDocument(doc_id="one", content="高度", vector=[1, 1, 0])])
    original = db.list_documents
    db.list_documents = AsyncMock(side_effect=original)
    retriever = HybridRetriever(db, FakeEmbedding(), FakeReranker(), "docs")
    await asyncio.gather(retriever.retrieve("高度"), retriever.retrieve("高度"))
    assert db.list_documents.await_count == 1
    await db.close()


async def test_feedback_file_lock_and_result_correspondence(tmp_path):
    file = tmp_path / "feedback.jsonl"
    collectors = [FeedbackCollector(file), FeedbackCollector(tmp_path / "child/../feedback.jsonl")]
    assert collectors[0]._lock is collectors[1]._lock
    await asyncio.gather(
        *[
            collectors[i % 2].collect_async(SearchFeedback(query=f"query{i}", results=[f"doc{i}"]))
            for i in range(100)
        ]
    )
    rows = [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 100
    assert all(row["results"] == [row["query"].replace("query", "doc")] for row in rows)


async def test_errors_logs_and_untrusted_parameters_do_not_leak(caplog):
    retriever = HybridRetriever(QdrantVectorDB(), FakeEmbedding(), FakeReranker())
    retriever.retrieve = AsyncMock(
        side_effect=RuntimeError("SECRET_CREDENTIAL http://user:SECRET_CREDENTIAL@host")
    )
    tool = SearchKnowledgeTool(retriever)
    for params in [{"query": "hello"}, {"query": "hello", "qdrant_url": "SECRET_CREDENTIAL"}]:
        result = await tool.run(**params)
        assert not result.success
        assert "SECRET_CREDENTIAL" not in str(result) + caplog.text
    assert retriever.retrieve.await_count == 1


async def test_close_waits_for_active_query_then_releases(tmp_path, neural):
    cap = KnowledgeCapability({"qdrant_path": str(tmp_path / "db")})
    await cap.start()
    entered, release = asyncio.Event(), asyncio.Event()

    async def retrieve(*args, **kwargs):
        entered.set()
        await release.wait()
        return []

    cap.retriever.retrieve = retrieve
    query = asyncio.create_task(cap.tools()[0].run(query="hello"))
    await entered.wait()
    close = asyncio.create_task(cap.close())
    await asyncio.sleep(0)
    assert not close.done()
    assert not (await cap.tools()[0].run(query="late")).success
    release.set()
    await query
    await close
    await cap.close()
    assert resources.live_resources() == 0


async def test_acquire_waits_until_retiring_database_closed(tmp_path, monkeypatch):
    key = ("retire-test", str(tmp_path))
    database = await resources.acquire(key, lambda: QdrantVectorDB(str(tmp_path)))
    entered, finish = asyncio.Event(), asyncio.Event()
    original_close = database.close

    async def held_close():
        entered.set()
        await finish.wait()
        await original_close()

    monkeypatch.setattr(database, "close", held_close)
    release = asyncio.create_task(resources.release(key))
    await entered.wait()
    acquire = asyncio.create_task(resources.acquire(key, lambda: QdrantVectorDB(str(tmp_path))))
    await asyncio.sleep(0)
    assert not acquire.done()
    finish.set()
    await release
    new = await acquire
    assert new is not database
    await resources.release(key)
    assert resources.live_resources() == 0


async def test_missing_collection_model_and_unreachable_remote_fail_safely(tmp_path, caplog):
    database = QdrantVectorDB(str(tmp_path / "missing-db"))
    retriever = HybridRetriever(database, FakeEmbedding(), FakeReranker(), "missing")
    assert not (await SearchKnowledgeTool(retriever).run(query="hello")).success
    await database.close()
    remote = QdrantVectorDB(url="http://127.0.0.1:1", api_key="CREDENTIAL_SENTINEL", timeout=1)
    retriever = HybridRetriever(remote, FakeEmbedding(), FakeReranker(), "missing")
    result = await SearchKnowledgeTool(retriever).run(query="hello")
    assert not result.success and "CREDENTIAL_SENTINEL" not in str(result) + caplog.text
    await remote.close()
    broken = FakeEmbedding()
    broken.embed_query = AsyncMock(side_effect=OSError("CREDENTIAL_SENTINEL"))
    db = QdrantVectorDB()
    await db.create_collection("docs", 3)
    await db.upsert("docs", [VectorDocument(doc_id="one", content="hello", vector=[1, 0, 0])])
    result = await SearchKnowledgeTool(HybridRetriever(db, broken, FakeReranker(), "docs")).run(
        query="hello"
    )
    assert not result.success and "CREDENTIAL_SENTINEL" not in str(result) + caplog.text
    await db.close()


async def test_partial_start_failure_preserves_existing_owner(tmp_path, neural, monkeypatch):
    settings = {"qdrant_path": str(tmp_path / "db")}
    first = KnowledgeCapability(settings)
    await first.start()
    await first.vectordb.create_collection("helpdesk_kb", 3)
    count = resources.live_resources()

    def broken(*args, **kwargs):
        raise RuntimeError("failure after resource acquisition")

    monkeypatch.setattr("capabilities.knowledge.capability.HybridRetriever", broken)
    failing = KnowledgeCapability(settings)
    with pytest.raises(RuntimeError):
        await failing.start()
    assert resources.live_resources() == count
    assert (await first.tools()[0].run(query="hello")).success
    await failing.close()
    await first.close()
    assert resources.live_resources() == 0


async def test_remote_timeout_is_finite_and_does_not_expose_key(caplog):
    import http.server
    import time

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(1.5)

        def log_message(self, *args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    database = QdrantVectorDB(
        url=f"http://127.0.0.1:{server.server_port}", api_key="TIMEOUT_SENTINEL", timeout=1
    )
    try:
        tool = SearchKnowledgeTool(HybridRetriever(database, FakeEmbedding(), FakeReranker()))
        async with asyncio.timeout(4):
            result = await tool.run(query="hello")
        assert not result.success and "TIMEOUT_SENTINEL" not in str(result) + caplog.text
    finally:
        await database.close()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=2)

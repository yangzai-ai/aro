"""Opt-in real database check, confined to a fresh disposable schema."""

import os
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest

from capabilities.session_persistence.backends.postgres import PostgresBackend
from core.kernel.engine import AgentEngine
from tests.capabilities.session_persistence.test_integration import config, settings, state
from tests.test_kernel_loop import FakeLLMProvider


async def test_real_postgres_crud_and_engine_reconstruction(tmp_path, monkeypatch):
    dsn = os.environ.get("ARO_TEST_SESSION_POSTGRES_URL")
    if not dsn:
        pytest.skip(
            "ARO_TEST_SESSION_POSTGRES_URL not configured; driver substitute tests run separately"
        )
    import asyncpg

    schema = "aro_session_test_" + uuid.uuid4().hex
    admin = await asyncpg.connect(dsn)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    parts = urlsplit(dsn)
    query = dict(parse_qsl(parts.query))
    query["search_path"] = schema
    isolated = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )
    monkeypatch.setenv("ARO_DB_URL", isolated)
    backend = PostgresBackend()
    try:
        await backend.initialize()
        await backend.save("crud", {"unicode": "阿洛"})
        assert await backend.load("crud") == {"unicode": "阿洛"}
        await backend.delete("crud")
        await backend.delete("crud")
        assert await backend.load("crud") is None
        assert await backend.list_expired(30) == []
        await backend.close()
        cfg = config(tmp_path, "postgres")
        for index in range(2):
            engine = await AgentEngine.create(settings(), cfg)
            engine.loop.llm = FakeLLMProvider("answer")
            try:
                restored = await engine.run("question", state())
                assert len(restored["messages"]) == (index + 1) * 2
            finally:
                await engine.close()
    finally:
        await backend.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()

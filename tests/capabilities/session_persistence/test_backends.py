"""Shared observable backend contract, including real SQLite persistence."""

import asyncio

import pytest

from capabilities.session_persistence.backends.memory import MemoryBackend
from capabilities.session_persistence.backends.postgres import PostgresBackend
from capabilities.session_persistence.backends.sqlite import SqliteBackend


@pytest.fixture(params=["memory", "sqlite", "postgres"])
async def backend(request, tmp_path, pg_double):
    value = {
        "memory": lambda: MemoryBackend(),
        "sqlite": lambda: SqliteBackend(tmp_path / "nested" / "sessions.db"),
        "postgres": lambda: PostgresBackend(),
    }[request.param]()
    await value.initialize()
    try:
        yield value
    finally:
        await value.close()


async def test_crud_upsert_and_deep_snapshot_isolation(backend):
    assert await backend.load("missing") is None
    original = {"messages": [{"nested": ["阿洛"]}], "context": {"thread_id": "s"}}
    await backend.save("s", original)
    original["messages"][0]["nested"].append("mutation")
    first = await backend.load("s")
    assert first["messages"][0]["nested"] == ["阿洛"]
    first["context"]["thread_id"] = "forged"
    assert (await backend.load("s"))["context"]["thread_id"] == "s"
    await backend.save("s", {"replacement": True})
    assert await backend.load("s") == {"replacement": True}
    await backend.delete("s")
    await backend.delete("s")
    assert await backend.load("s") is None


async def test_session_keys_are_bound_parameters_not_sql(backend):
    key = "s'; DROP TABLE sessions; --"
    await backend.save(key, {"value": "special"})
    await backend.save("other", {"value": "safe"})
    assert await backend.load(key) == {"value": "special"}
    await backend.delete(key)
    assert await backend.load("other") == {"value": "safe"}


async def test_retention_boundary_and_startup_purge(backend, monkeypatch):
    import time

    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    await backend.save("old", {"n": 1})
    monkeypatch.setattr(time, "time", lambda: now + 30 * 86400)
    assert await backend.load("old") is None
    assert await backend.list_expired(30) == ["old"]
    await backend.save("new", {"n": 2})
    await backend.close()
    await backend.initialize()
    assert await backend.list_expired(30) == []
    assert await backend.load("new") == {"n": 2}


async def test_idempotent_initialize_close_and_reopen(backend):
    await backend.initialize()
    await backend.save("s", {"n": 1})
    await asyncio.gather(backend.close(), backend.close())
    await backend.close()
    with pytest.raises(RuntimeError):
        await backend.load("s")
    await backend.initialize()
    assert await backend.load("s") == {"n": 1}


async def test_non_json_state_rejected_without_clobbering_snapshot(backend):
    await backend.save("s", {"valid": True})
    with pytest.raises((TypeError, ValueError)):
        await backend.save("s", {"bad": object()})
    assert await backend.load("s") == {"valid": True}


async def test_explicit_memory_instances_do_not_share_data():
    first, second = MemoryBackend(), MemoryBackend()
    await first.initialize()
    await second.initialize()
    await first.save("s", {"x": 1})
    assert await second.load("s") is None
    await first.close()
    await second.close()


async def test_sqlite_reconstructs_instance_from_disk(tmp_path):
    path = tmp_path / "db.sqlite"
    first = SqliteBackend(path)
    await first.initialize()
    await first.save("s", {"state": "durable"})
    await first.close()
    second = SqliteBackend(path)
    await second.initialize()
    assert await second.load("s") == {"state": "durable"}
    await second.close()


async def test_postgres_uses_env_and_pool_and_sanitizes_failures(pg_double):
    rows, pools = pg_double
    value = PostgresBackend()
    await value.initialize()
    pool = pools[0]
    await value.initialize()
    assert len(pools) == 1
    pool.fail = RuntimeError("postgresql://test:secret@unused.test/isolated")
    for operation in (
        value.save("s", {}),
        value.load("s"),
        value.delete("s"),
        value.list_expired(30),
    ):
        with pytest.raises(RuntimeError) as error:
            await operation
        assert "secret" not in str(error.value) and error.value.__suppress_context__
    await value.close()
    assert pool.closed and pool.close_count == 1


async def test_postgres_missing_environment_fails_without_connecting(pg_double, monkeypatch):
    monkeypatch.delenv("ARO_DB_URL")
    value = PostgresBackend()
    with pytest.raises(ValueError, match="ARO_DB_URL"):
        await value.initialize()
    assert not pg_double[1]
    await value.close()


async def test_postgres_partial_start_failure_closes_pool(pg_double, monkeypatch):
    import asyncpg

    factory = asyncpg.create_pool

    def broken(*args, **kwargs):
        pool = factory(*args, **kwargs)
        pool.fail = ValueError("secret credentials")
        return pool

    monkeypatch.setattr(asyncpg, "create_pool", broken)
    value = PostgresBackend()
    with pytest.raises(RuntimeError, match="initialization failed"):
        await value.initialize()
    assert pg_double[1][0].closed and value._pool is None
    await value.close()


async def test_postgres_cancelled_start_closes_pool(pg_double, monkeypatch):
    import asyncpg

    factory = asyncpg.create_pool

    def blocking(*args, **kwargs):
        pool = factory(*args, **kwargs)
        pool.block = asyncio.Event()
        return pool

    monkeypatch.setattr(asyncpg, "create_pool", blocking)
    value = PostgresBackend()
    task = asyncio.create_task(value.initialize())
    while not pg_double[1]:
        await asyncio.sleep(0)
    await pg_double[1][0].entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pg_double[1][0].closed and value._pool is None


async def test_sqlite_cancelled_connect_is_drained_and_closed(tmp_path, monkeypatch):
    import aiosqlite

    original = aiosqlite.connect
    entered, release = asyncio.Event(), asyncio.Event()
    connections = []

    async def delayed(*args, **kwargs):
        connection = await original(*args, **kwargs)
        connections.append(connection)
        entered.set()
        await release.wait()
        return connection

    monkeypatch.setattr(aiosqlite, "connect", delayed)
    value = SqliteBackend(tmp_path / "cancel.db")
    task = asyncio.create_task(value.initialize())
    await entered.wait()
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert value._connection is None
    with pytest.raises(ValueError, match="no active connection"):
        await connections[0].execute("SELECT 1")
    await value.close()


async def test_postgres_pool_constructor_error_is_sanitized(monkeypatch):
    import asyncpg

    monkeypatch.setenv("ARO_DB_URL", "postgresql://secret-password@invalid")

    def fail(*args, **kwargs):
        raise ValueError("secret-password in driver exception")

    monkeypatch.setattr(asyncpg, "create_pool", fail)
    backend = PostgresBackend()
    with pytest.raises(RuntimeError) as error:
        await backend.initialize()
    assert "secret-password" not in str(error.value) and error.value.__suppress_context__
    assert backend._pool is None
    await backend.close()


@pytest.mark.parametrize("kind", ["sqlite", "postgres"])
async def test_cancelled_close_waiter_does_not_leak_connection(
    tmp_path, pg_double, monkeypatch, kind
):
    entered, release = asyncio.Event(), asyncio.Event()
    backend = SqliteBackend(tmp_path / "close.db") if kind == "sqlite" else PostgresBackend()
    await backend.initialize()
    resource = backend._connection if kind == "sqlite" else backend._pool
    close = resource.close
    count = 0

    async def blocked_close():
        nonlocal count
        count += 1
        entered.set()
        await release.wait()
        await close()

    monkeypatch.setattr(resource, "close", blocked_close)
    first = asyncio.create_task(backend.close())
    await asyncio.wait_for(entered.wait(), 2)
    second = asyncio.create_task(backend.close())
    first.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    assert count == 1
    assert (backend._connection if kind == "sqlite" else backend._pool) is None
    await backend.close()

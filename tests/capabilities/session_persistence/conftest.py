"""Driver-level PostgreSQL substitute; SQL arguments are kept separate from text."""

import asyncio

import pytest


class PoolDouble:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.closed = False
        self.close_count = 0
        self.fail = None
        self.block = None
        self.entered = asyncio.Event()

    def __await__(self):
        async def ready():
            return self

        return ready().__await__()

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        if self.fail is not None:
            raise self.fail
        if self.block is not None:
            self.entered.set()
            await self.block.wait()
        if sql.startswith("CREATE TABLE"):
            assert not args
        elif sql.startswith("INSERT"):
            assert "$1" in sql and len(args) == 3
            self.rows[args[0]] = (args[1], args[2])
        elif sql.startswith("DELETE") and "updated_at" in sql:
            for key in list(self.rows):
                if self.rows[key][1] <= args[0]:
                    del self.rows[key]
        elif sql.startswith("DELETE"):
            assert "$1" in sql and len(args) == 1
            self.rows.pop(args[0], None)
        else:
            raise AssertionError(sql)
        return "OK"

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        if self.fail:
            raise self.fail
        assert "session_id=$1" in sql and "updated_at>$2" in sql
        row = self.rows.get(args[0])
        return {"data_json": row[0]} if row and row[1] > args[1] else None

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        if self.fail:
            raise self.fail
        assert "updated_at<=$1" in sql
        return [{"session_id": key} for key, row in sorted(self.rows.items()) if row[1] <= args[0]]

    async def close(self):
        self.close_count += 1
        self.closed = True

    def terminate(self):
        self.closed = True


@pytest.fixture
def pg_double(monkeypatch):
    import asyncpg

    rows, pools = {}, []

    def create_pool(dsn, **kwargs):
        assert dsn == "postgresql://test:secret@unused.test/isolated"
        assert kwargs == {"min_size": 0, "max_size": 4, "command_timeout": 30, "timeout": 30}
        pool = PoolDouble(rows)
        pools.append(pool)
        return pool

    monkeypatch.setenv("ARO_DB_URL", "postgresql://test:secret@unused.test/isolated")
    monkeypatch.setattr(asyncpg, "create_pool", create_pool)
    return rows, pools

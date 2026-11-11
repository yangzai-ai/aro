"""SQLite snapshots with atomic autocommit statements and cancellation-safe close."""

import asyncio
import time
from pathlib import Path

import aiosqlite

from core.kernel.lifecycle import wait_for_cleanup

from .base import SessionData, cutoff, decode, encode


class SqliteBackend:
    def __init__(self, path: str | Path | None = None, retention_days: int = 30) -> None:
        self.path = str(Path(path or "~/.aro/sessions.db").expanduser())
        self.retention_days = retention_days
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._lock:
            if self._connection is not None:
                return
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)

            # Retrieve the connection even if the caller cancels during connect,
            # then close it. aiosqlite owns a worker thread until close completes.
            async def connect() -> aiosqlite.Connection:
                return await aiosqlite.connect(self.path, isolation_level=None, timeout=30)

            task = asyncio.create_task(connect())
            try:
                self._connection = await asyncio.shield(task)
                async with self._connection.execute(
                    "CREATE TABLE IF NOT EXISTS sessions ("
                    "session_id TEXT PRIMARY KEY, data_json TEXT NOT NULL, updated_at REAL NOT NULL)"
                ):
                    pass
                async with self._connection.execute(
                    "DELETE FROM sessions WHERE updated_at <= ?", (cutoff(self.retention_days),)
                ):
                    pass
            except BaseException:
                # Drain an outstanding connect before collecting its result.
                try:
                    await wait_for_cleanup(task)
                finally:
                    if task.done() and not task.cancelled() and task.exception() is None:
                        await wait_for_cleanup(asyncio.create_task(task.result().close()))
                    self._connection = None
                raise

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("Session backend is not initialized")
        return self._connection

    async def save(self, session_id: str, data: SessionData) -> None:
        serialized = encode(data)
        async with self._lock, self._require_connection().execute(
            "INSERT INTO sessions(session_id,data_json,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET data_json=excluded.data_json, "
            "updated_at=excluded.updated_at",
            (session_id, serialized, time.time()),
        ):
            pass

    async def load(self, session_id: str) -> SessionData | None:
        async with self._lock:
            async with self._require_connection().execute(
                "SELECT data_json FROM sessions WHERE session_id=? AND updated_at>?",
                (session_id, cutoff(self.retention_days)),
            ) as cursor:
                row = await cursor.fetchone()
            return decode(row[0]) if row is not None else None

    async def delete(self, session_id: str) -> None:
        async with self._lock, self._require_connection().execute(
            "DELETE FROM sessions WHERE session_id=?", (session_id,)
        ):
            pass

    async def list_expired(self, retention_days: int) -> list[str]:
        async with self._lock:
            async with self._require_connection().execute(
                "SELECT session_id FROM sessions WHERE updated_at<=? ORDER BY session_id",
                (cutoff(retention_days),),
            ) as cursor:
                rows = await cursor.fetchall()
            return [str(row[0]) for row in rows]

    async def close(self) -> None:
        async def release() -> None:
            async with self._lock:
                if self._connection is not None:
                    await self._connection.close()
                    self._connection = None

        await wait_for_cleanup(asyncio.create_task(release()))

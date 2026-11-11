"""Pooled PostgreSQL backend. DSN is deployment-only ARO_DB_URL."""

from __future__ import annotations

import asyncio
import os
import time

import asyncpg

from core.kernel.lifecycle import wait_for_cleanup

from .base import SessionData, cutoff, decode, encode


class PostgresBackend:
    def __init__(self, retention_days: int = 30) -> None:
        self.retention_days = retention_days
        self._pool: asyncpg.Pool[asyncpg.Record] | None = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._lock:
            if self._pool is not None:
                return
            dsn = os.environ.get("ARO_DB_URL")
            if not dsn:
                raise ValueError("PostgreSQL session backend requires ARO_DB_URL")
            # With min_size=0 the pool is initialized without opening a socket;
            # subsequent queries establish connections owned by this pool.
            try:
                pool = asyncpg.create_pool(
                    dsn, min_size=0, max_size=4, command_timeout=30, timeout=30
                )
                await pool
            except Exception:
                raise RuntimeError("PostgreSQL session initialization failed") from None
            self._pool = pool
            try:
                await pool.execute(
                    "CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, "
                    "data_json TEXT NOT NULL, updated_at DOUBLE PRECISION NOT NULL)"
                )
                await pool.execute(
                    "DELETE FROM sessions WHERE updated_at <= $1", cutoff(self.retention_days)
                )
            except BaseException as exc:
                try:
                    await wait_for_cleanup(asyncio.create_task(self._release_pool(pool)))
                finally:
                    self._pool = None
                if isinstance(exc, Exception):
                    raise RuntimeError("PostgreSQL session initialization failed") from None
                raise

    @staticmethod
    async def _release_pool(pool: asyncpg.Pool[asyncpg.Record]) -> None:
        # close() can hang waiting for checked-out connections; bound shutdown.
        try:
            async with asyncio.timeout(30):
                await pool.close()
        except asyncio.CancelledError:
            pool.terminate()
            raise
        except Exception:
            pool.terminate()
            raise RuntimeError("PostgreSQL session pool cleanup failed") from None

    def _require_pool(self) -> asyncpg.Pool[asyncpg.Record]:
        if self._pool is None:
            raise RuntimeError("Session backend is not initialized")
        return self._pool

    async def save(self, session_id: str, data: SessionData) -> None:
        serialized = encode(data)
        async with self._lock:
            try:
                await self._require_pool().execute(
                    "INSERT INTO sessions(session_id,data_json,updated_at) VALUES($1,$2,$3) "
                    "ON CONFLICT(session_id) DO UPDATE SET data_json=excluded.data_json, "
                    "updated_at=excluded.updated_at",
                    session_id,
                    serialized,
                    time.time(),
                )
            except Exception:
                raise RuntimeError("PostgreSQL session save failed") from None

    async def load(self, session_id: str) -> SessionData | None:
        async with self._lock:
            try:
                row = await self._require_pool().fetchrow(
                    "SELECT data_json FROM sessions WHERE session_id=$1 AND updated_at>$2",
                    session_id,
                    cutoff(self.retention_days),
                )
                return decode(row["data_json"]) if row is not None else None
            except Exception:
                raise RuntimeError("PostgreSQL session load failed") from None

    async def delete(self, session_id: str) -> None:
        async with self._lock:
            try:
                await self._require_pool().execute(
                    "DELETE FROM sessions WHERE session_id=$1", session_id
                )
            except Exception:
                raise RuntimeError("PostgreSQL session delete failed") from None

    async def list_expired(self, retention_days: int) -> list[str]:
        deadline = cutoff(retention_days)
        async with self._lock:
            try:
                rows = await self._require_pool().fetch(
                    "SELECT session_id FROM sessions WHERE updated_at<=$1 ORDER BY session_id",
                    deadline,
                )
                return [str(row["session_id"]) for row in rows]
            except Exception:
                raise RuntimeError("PostgreSQL session expiry query failed") from None

    async def close(self) -> None:
        async def release() -> None:
            async with self._lock:
                if self._pool is not None:
                    pool, self._pool = self._pool, None
                    await self._release_pool(pool)

        await wait_for_cleanup(asyncio.create_task(release()))

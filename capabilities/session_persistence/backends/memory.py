"""Per-instance, process-local reference backend. Closing does not erase data."""

import time

from .base import SessionData, cutoff, decode, encode


class MemoryBackend:
    def __init__(self, retention_days: int = 30) -> None:
        self.retention_days = retention_days
        self._records: dict[str, tuple[str, float]] = {}
        self._ready = False

    async def initialize(self) -> None:
        for session_id in await self.list_expired(self.retention_days):
            self._records.pop(session_id, None)
        self._ready = True

    def _require_ready(self) -> None:
        if not self._ready:
            raise RuntimeError("Session backend is not initialized")

    async def save(self, session_id: str, data: SessionData) -> None:
        self._require_ready()
        self._records[session_id] = (encode(data), time.time())

    async def load(self, session_id: str) -> SessionData | None:
        self._require_ready()
        record = self._records.get(session_id)
        if record is None or record[1] <= cutoff(self.retention_days):
            return None
        return decode(record[0])

    async def delete(self, session_id: str) -> None:
        self._require_ready()
        self._records.pop(session_id, None)

    async def list_expired(self, retention_days: int) -> list[str]:
        deadline = cutoff(retention_days)
        return sorted(key for key, (_, updated) in self._records.items() if updated <= deadline)

    async def close(self) -> None:
        self._ready = False

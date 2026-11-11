"""Backend contract and common JSON snapshot format."""

import json
import time
from typing import Any, Protocol

SessionData = dict[str, Any]


class Backend(Protocol):
    async def initialize(self) -> None: ...
    async def save(self, session_id: str, data: SessionData) -> None: ...
    async def load(self, session_id: str) -> SessionData | None: ...
    async def delete(self, session_id: str) -> None: ...
    async def list_expired(self, retention_days: int) -> list[str]: ...
    async def close(self) -> None: ...


def cutoff(retention_days: int) -> float:
    if retention_days <= 0:
        raise ValueError("retention_days must be positive")
    return time.time() - retention_days * 86400


def encode(data: SessionData) -> str:
    # No repr/default=str fallback: unsupported objects must fail, never silently
    # corrupt a state snapshot. Memory uses the same format as durable backends.
    return json.dumps(data, ensure_ascii=False, allow_nan=False)


def decode(value: str) -> SessionData:
    data: Any = json.loads(value)
    if not isinstance(data, dict):
        raise ValueError("Session snapshot must be a JSON object")
    return data

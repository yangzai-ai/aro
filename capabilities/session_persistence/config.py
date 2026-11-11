"""Validated deployment configuration; credentials never come from model input."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SessionPersistenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: Literal["memory", "sqlite", "postgres"] = "memory"
    sqlite_path: str | None = Field(default=None, min_length=1)
    retention_days: int = Field(default=30, gt=0)
    delete_on_session_close: bool = False

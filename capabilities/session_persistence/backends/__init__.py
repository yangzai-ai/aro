"""Backend selection without importing unselected optional drivers."""

from capabilities.session_persistence.config import SessionPersistenceConfig

from .base import Backend


def create_backend(config: SessionPersistenceConfig) -> Backend:
    if config.backend == "sqlite":
        from .sqlite import SqliteBackend

        return SqliteBackend(config.sqlite_path, config.retention_days)
    if config.backend == "postgres":
        from .postgres import PostgresBackend

        return PostgresBackend(config.retention_days)
    from .memory import MemoryBackend

    return MemoryBackend(config.retention_days)

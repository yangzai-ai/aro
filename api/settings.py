"""Local API configuration; secrets stay on the backend."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class APISettings(BaseSettings):
    """Single-process development settings loaded from environment or .env."""

    model_config = SettingsConfigDict(env_prefix="ARO_API_", env_file=".env", extra="ignore")
    config: Path = Path("projects/helpdesk/config/agent_api.yml")
    token: SecretStr | None = None
    session_ttl_seconds: float = Field(default=3600, gt=0)
    max_sessions: int = Field(default=64, gt=0)
    max_messages: int = Field(default=300, gt=0)
    turn_timeout_seconds: float = Field(default=600, gt=0)
    # Trusted server-side local demo identity; never accepted from chat JSON/model args.
    user_id: str | None = Field(default=None, min_length=1, max_length=128)
    tenant_id: str = Field(default="local", min_length=1, max_length=128)

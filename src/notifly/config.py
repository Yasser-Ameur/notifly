"""Application settings, loaded from environment variables with a ``NOTIFLY_`` prefix."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NOTIFLY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = Environment.DEVELOPMENT
    debug: bool = Field(default=False, description="Enable verbose debug output.")

    # --- Infrastructure ---
    database_url: str = Field(
        default="sqlite+aiosqlite:///./notifly.db",
        description="Async SQLAlchemy database URL (PostgreSQL in production).",
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis URL used for rate limiting and the ARQ job queue.",
    )
    database_echo: bool = Field(default=False, description="Echo SQL statements.")

    # --- API keys ---
    api_key_prefix: str = Field(default="notifly_", description="Prefix for generated API keys.")
    api_key_hash_iterations: int = Field(default=120_000, description="PBKDF2 iterations.")

    # --- Scheduler ---
    outbox_poll_interval: float = Field(default=2.0, description="Outbox relay poll interval (s).")
    scheduled_poll_interval: float = Field(
        default=15.0, description="Scheduled notification poll interval (s)."
    )
    retry_poll_interval: float = Field(default=30.0, description="Retry poll interval (s).")
    outbox_batch_size: int = Field(default=100, description="Outbox relay batch size.")

    # --- Observability ---
    log_level: str = Field(default="INFO")
    json_logs: bool = Field(default=False, description="Emit structured JSON logs.")
    metrics_enabled: bool = Field(default=True, description="Expose Prometheus metrics.")

    # --- Default retry policy ---
    default_max_attempts: int = Field(default=3, ge=1)
    default_retry_backoff_seconds: float = Field(default=5.0, ge=0.0)
    retry_backoff_factor: float = Field(default=2.0, ge=1.0)
    retry_max_backoff_seconds: float = Field(default=300.0, ge=1.0)

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION


@lru_cache
def get_settings() -> Settings:
    return Settings()

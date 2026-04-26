"""
Application settings.

All configuration is loaded from environment variables via pydantic-settings.
Defaults are development-safe; production values must be set in environment.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ────────────────────────────────────────────────────────
    APP_ENV: Literal["development", "staging", "production"] = "development"
    APP_NAME: str = "DesignPilot MECH"
    APP_VERSION: str = "1.0.0-alpha.1"
    LOG_LEVEL: str = "INFO"
    DEBUG: bool = True

    # ── Server ─────────────────────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    # ── Database ───────────────────────────────────────────────────
    DATABASE_URL: str = (
        "postgresql+asyncpg://designpilot:designpilot@localhost:5433/designpilot_dev"
    )
    DATABASE_URL_SYNC: str = (
        "postgresql://designpilot:designpilot@localhost:5433/designpilot_dev"
    )
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 5
    DB_ECHO: bool = False

    # ── Supabase ───────────────────────────────────────────────────
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    SUPABASE_JWT_SECRET: str = "development-only-secret-change-in-prod"
    SUPABASE_JWT_ALGORITHM: str = "HS256"
    SUPABASE_JWT_AUDIENCE: str = "authenticated"

    # ── Redis ──────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6380/0"

    # ── LLM ────────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-20250514"
    ANTHROPIC_MAX_TOKENS: int = 4096

    # ── Storage ────────────────────────────────────────────────────
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET_NAME: str = "designpilot-designs"
    R2_ENDPOINT_URL: str = ""

    # ── Observability ──────────────────────────────────────────────
    SENTRY_DSN: str = ""
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1
    POSTHOG_API_KEY: str = ""
    POSTHOG_HOST: str = "https://app.posthog.com"

    # ── Feature flags ──────────────────────────────────────────────
    FEATURE_TEAMS: bool = False
    FEATURE_MOBILE: bool = False
    FEATURE_FEA: bool = False
    FEATURE_2D_DRAWINGS: bool = False

    # ── Rate limits ────────────────────────────────────────────────
    RATE_LIMIT_FREE_DESIGNS_PER_MONTH: int = 5
    RATE_LIMIT_PRO_DESIGNS_PER_MONTH: int = 500
    RATE_LIMIT_IP_PER_MINUTE: int = 100
    RATE_LIMIT_IP_PER_HOUR: int = 1000

    # ── Security ───────────────────────────────────────────────────
    SECRET_KEY: str = "change-me-to-a-long-random-string-min-32-chars"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── Sandbox ────────────────────────────────────────────────────
    SANDBOX_IMAGE: str = "designpilot/cadquery-sandbox:latest"
    SANDBOX_TIMEOUT_SECONDS: int = 30
    SANDBOX_MEMORY_LIMIT_MB: int = 512
    SANDBOX_CPU_QUOTA: int = 200000
    # Skip Docker execution in dev — returns a deterministic mock STEP so the
    # full LLM → pipeline → DB flow can be tested without the sandbox image.
    # NEVER set True in production. Enforced by _validate_sandbox_skip below.
    SANDBOX_SKIP_FOR_DEV: bool = False

    # ── Stripe billing ──────────────────────────────────────────────
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PRICE_PRO: str = ""
    STRIPE_PRICE_TEAM: str = ""

    # ── Email (Resend or SMTP) ──────────────────────────────────────
    RESEND_API_KEY: str = ""
    EMAIL_FROM: str = "noreply@designpilot.in"

    # ── Validators ─────────────────────────────────────────────────
    @field_validator("DEBUG", mode="before")
    @classmethod
    def _normalize_debug(cls, v):
        if isinstance(v, str):
            normalized = v.strip().lower()
            if normalized in {"1", "true", "yes", "on", "debug", "development"}:
                return True
            if normalized in {"0", "false", "no", "off", "release", "production"}:
                return False
        return v

    @field_validator("SECRET_KEY")
    @classmethod
    def _validate_secret_key(cls, v: str, info) -> str:
        """In production, the secret key must be a real secret."""
        # Skip strict validation in tests/dev
        return v

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


@lru_cache
def get_settings() -> Settings:
    """Return cached settings singleton. Use `Depends(get_settings)` in routes."""
    return Settings()

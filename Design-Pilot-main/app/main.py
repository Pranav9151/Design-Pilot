"""
FastAPI application entrypoint.

Run locally:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

In production, uvicorn is launched by the container.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import sentry_sdk
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app import __version__
from app.api.v1 import api_router
from app.audit.middleware import AuditLogMiddleware
from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.logging import configure_logging

configure_logging()
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    if settings.SENTRY_DSN:
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
            environment=settings.APP_ENV,
            release=__version__,
        )
        logger.info("sentry_initialized")

    logger.info(
        "app_starting",
        app=settings.APP_NAME,
        version=settings.APP_VERSION,
        env=settings.APP_ENV,
    )
    yield
    logger.info("app_shutting_down")
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=(
            "DesignPilot MECH — AI-powered engineering design tool for "
            "mechanical design engineers. v1.0 scope: brackets only."
        ),
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # ── Security headers ─────────────────────────────────────────
    app.add_middleware(SecurityHeadersMiddleware)

    # ── CORS ─────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    # ── Audit middleware (always on, from day 1) ─────────────────
    app.add_middleware(AuditLogMiddleware)

    # ── Routes ───────────────────────────────────────────────────
    app.include_router(api_router)

    # Top-level /health for simpler liveness probes
    @app.get("/health", include_in_schema=False)
    async def _health_top():
        return {"status": "ok", "version": __version__}

    return app


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach baseline security headers to every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=(), payment=()"
        )
        # Strict-Transport-Security is only meaningful over HTTPS;
        # production terminator (Cloudflare / Fly.io) can also add it.
        settings = get_settings()
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
            )
        return response


app = create_app()

"""Health & readiness endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import get_db

router = APIRouter()


@router.get("/health")
async def health(settings: Settings = Depends(get_settings)) -> dict:
    """Liveness check: process is up. No DB touched."""
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "env": settings.APP_ENV,
    }


@router.get("/ready")
async def ready(
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Readiness check: DB query works."""
    try:
        result = await db.execute(text("SELECT 1"))
        _ = result.scalar_one()
        return JSONResponse({"status": "ready", "db": "ok"})
    except Exception as exc:
        return JSONResponse(
            {"status": "not_ready", "db": "error", "error": str(exc)},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

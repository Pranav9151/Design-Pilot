"""
Redis connection helper.

Provides a lazy async singleton Redis client and a FastAPI dependency.
Falls back to an in-process fakeredis when REDIS_URL is empty (dev/test).
"""
from __future__ import annotations

from typing import AsyncIterator

import structlog
from redis.asyncio import Redis

from app.core.config import get_settings

logger = structlog.get_logger(__name__)


_redis_singleton: Redis | None = None


async def get_redis() -> Redis:
    """Return the process-wide Redis client, lazily created on first call.

    In dev/test where REDIS_URL is empty we use fakeredis so the app still
    boots and tests still run. Production requires a real Redis.
    """
    global _redis_singleton
    if _redis_singleton is not None:
        return _redis_singleton

    settings = get_settings()
    redis_url = settings.REDIS_URL or ""

    if not redis_url:
        # No real Redis configured — use fakeredis in-process.
        import fakeredis.aioredis
        logger.warning("redis_not_configured_using_fakeredis", env=settings.APP_ENV)
        _redis_singleton = fakeredis.aioredis.FakeRedis(decode_responses=False)
    else:
        _redis_singleton = Redis.from_url(redis_url, decode_responses=False)
        logger.info("redis_connected", url_host=_extract_host(redis_url))

    return _redis_singleton


async def close_redis() -> None:
    """Called from app shutdown (app.main lifespan)."""
    global _redis_singleton
    if _redis_singleton is not None:
        try:
            await _redis_singleton.aclose()
        except Exception as exc:  # pragma: no cover
            logger.warning("redis_close_failed", error=str(exc))
        _redis_singleton = None


async def redis_dependency() -> AsyncIterator[Redis]:
    """FastAPI dependency — use via `Depends(redis_dependency)`."""
    client = await get_redis()
    yield client
    # We don't close here — the client is a process-wide singleton.


def _extract_host(url: str) -> str:
    """For logging: 'redis://user:pw@host:port/0' → 'host:port'."""
    try:
        _, rest = url.split("://", 1)
        if "@" in rest:
            rest = rest.split("@", 1)[1]
        return rest.split("/", 1)[0]
    except Exception:
        return "<unparseable>"

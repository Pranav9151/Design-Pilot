"""
Audit middleware.

Automatically logs every authenticated API request to the audit log.
Domain-specific events (design.create.complete, etc.) are logged
explicitly from route handlers via the audit_service.

We skip logging for:
- OPTIONS / health / readiness endpoints
- Static assets
- Requests without an authenticated user (those are logged separately as auth failures)
"""
from __future__ import annotations

import time
from uuid import UUID, uuid4

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.audit.service import audit_service
from app.core.db import get_session_factory

logger = structlog.get_logger(__name__)


# Paths we don't audit (high volume, low signal)
_SKIP_PATHS: frozenset[str] = frozenset({
    "/health",
    "/ready",
    "/metrics",
    "/docs",
    "/openapi.json",
    "/redoc",
})

_SKIP_METHODS: frozenset[str] = frozenset({"OPTIONS", "HEAD"})


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Log every authenticated, state-changing request."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Attach a request_id for correlation with app logs
        request_id = str(uuid4())
        request.state.request_id = request_id

        structlog.contextvars.bind_contextvars(request_id=request_id)

        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        response.headers["X-Request-ID"] = request_id

        # Decide whether to audit this request
        path = request.url.path
        if (
            request.method in _SKIP_METHODS
            or path in _SKIP_PATHS
            or path.startswith(("/static/", "/assets/"))
        ):
            structlog.contextvars.clear_contextvars()
            return response

        # Only audit state-changing methods + sensitive reads
        state_changing = request.method in {"POST", "PUT", "PATCH", "DELETE"}
        if not state_changing and not path.startswith("/api/v1/audit"):
            structlog.contextvars.clear_contextvars()
            return response

        # Actor: may be None if unauthenticated (still audit those)
        actor_id_raw = getattr(request.state, "user_id", None)
        actor_id: UUID | None = None
        if actor_id_raw:
            try:
                actor_id = UUID(actor_id_raw)
            except (ValueError, TypeError):
                actor_id = None

        # Fire-and-forget: use our own session so we never roll back
        # the caller's transaction if audit fails.
        factory = get_session_factory()
        async with factory() as session:
            try:
                await audit_service.log(
                    session=session,
                    actor_user_id=actor_id,
                    action=f"http.{request.method.lower()}",
                    resource_type="http_request",
                    resource_id=path,
                    metadata={
                        "path": path,
                        "query": str(request.url.query),
                        "elapsed_ms": elapsed_ms,
                    },
                    ip_address=_client_ip(request),
                    user_agent=request.headers.get("user-agent", "")[:500],
                    status_code=response.status_code,
                )
                await session.commit()
            except Exception as exc:  # pragma: no cover
                # Never let audit failure break the response
                logger.error("audit_middleware_failed", error=str(exc))

        structlog.contextvars.clear_contextvars()
        return response


def _client_ip(request: Request) -> str:
    """Best-effort client IP extraction, honoring X-Forwarded-For."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""

"""
Audit service: writes append-only records to `audit_log`.

Usage from route code:
    from app.audit import audit_service
    await audit_service.log(
        session=db,
        actor_user_id=user.id,
        action="design.create.complete",
        resource_type="design",
        resource_id=design.id,
        metadata={"prompt_preview": prompt[:100]},
    )

The middleware handles the common case of "log this request"; service
calls handle domain-specific semantic events (design.create.complete,
permission.granted, etc.).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


# Redacted field names we never log in payloads, even by accident.
_REDACT_KEYS = frozenset({
    "password", "passwd", "secret", "api_key", "apikey", "token",
    "access_token", "refresh_token", "authorization", "cookie",
    "credit_card", "cvv", "ssn",
})


def _redact(obj: Any, depth: int = 0) -> Any:
    """Recursively redact sensitive keys in dict/list payloads."""
    if depth > 6:
        return "<redacted-depth-limit>"
    if isinstance(obj, dict):
        return {
            k: ("<redacted>" if k.lower() in _REDACT_KEYS else _redact(v, depth + 1))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(x, depth + 1) for x in obj]
    return obj


class AuditService:
    """Append-only audit log writer.

    Writes to the `audit_log` table. The DB policy revokes UPDATE/DELETE
    so records cannot be tampered with by the application layer.
    """

    async def log(
        self,
        *,
        session: AsyncSession,
        actor_user_id: UUID | None,
        action: str,
        resource_type: str | None = None,
        resource_id: UUID | str | None = None,
        metadata: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        status_code: int | None = None,
        team_id: UUID | None = None,
    ) -> UUID:
        """Insert an audit record. Returns the record's UUID."""
        event_id = uuid4()
        safe_metadata = _redact(metadata or {})

        # Use raw SQL to keep this module decoupled from ORM models
        # (audit_log is append-only; no ORM read path needed).
        stmt = text(
            """
            INSERT INTO audit_log (
                id, actor_user_id, team_id, action, resource_type, resource_id,
                metadata, ip_address, user_agent, status_code, created_at
            ) VALUES (
                :id, :actor_user_id, :team_id, :action, :resource_type, :resource_id,
                CAST(:metadata AS JSONB), :ip_address, :user_agent, :status_code, :created_at
            )
            """
        )

        import json as _json

        await session.execute(
            stmt,
            {
                "id": event_id,
                "actor_user_id": actor_user_id,
                "team_id": team_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": str(resource_id) if resource_id is not None else None,
                "metadata": _json.dumps(safe_metadata),
                "ip_address": ip_address,
                "user_agent": user_agent,
                "status_code": status_code,
                "created_at": datetime.now(timezone.utc),
            },
        )
        # Commit is the caller's responsibility (they own the transaction).

        logger.info(
            "audit",
            event_id=str(event_id),
            actor=str(actor_user_id) if actor_user_id else None,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id else None,
            status_code=status_code,
        )
        return event_id


# Module-level singleton. FastAPI routes can import directly.
audit_service = AuditService()

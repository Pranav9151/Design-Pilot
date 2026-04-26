"""
AuditLog ORM model.

Append-only. Inserts happen via `app.audit.service.AuditService` (raw SQL).
The DB migration revokes UPDATE/DELETE on this table to the app role, so
the ORM can only ever read these records. Exposing this as an ORM model
lets admin routes query the log; it does NOT let application code mutate it.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)

    actor_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    team_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )

    action: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    resource_id: Mapped[str | None] = mapped_column(String(500), nullable=True)

    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)

    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

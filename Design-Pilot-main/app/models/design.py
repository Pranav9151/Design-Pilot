"""
Design ORM model — the central entity.

Stores the full generation result: CadQuery code, parameters, STEP/GLB
URLs, all three Triple-Lock analysis outputs, confidence score, and
provenance.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Design(Base):
    __tablename__ = "designs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Ownership — owner_id is the individual user; team_id is NULL for solo (v1.0 default).
    owner_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )

    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    part_type: Mapped[str] = mapped_column(String(100), nullable=False, default="bracket")
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")

    # Generation artifacts
    cadquery_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    step_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    glb_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Triple-Lock results
    lock1_results: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    lock2_results: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    lock3_results: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Analysis
    material_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("materials.id", ondelete="RESTRICT"), nullable=True
    )
    simulation: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    dfm: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    cost: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    assumptions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    # Metadata
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("designs.id", ondelete="SET NULL"), nullable=True
    )
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    owner: Mapped["User"] = relationship(  # noqa: F821
        "User", back_populates="designs", foreign_keys=[owner_id]
    )
    diary_entries: Mapped[list["DesignDiary"]] = relationship(
        "DesignDiary", back_populates="design", cascade="all, delete-orphan",
        order_by="DesignDiary.created_at",
    )
    feedback: Mapped[list["DesignFeedback"]] = relationship(
        "DesignFeedback", back_populates="design", cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 100)",
            name="ck_designs_confidence_range",
        ),
        CheckConstraint(
            "status IN ('draft','generated','analyzed','finalized','archived','failed')",
            name="ck_designs_status_enum",
        ),
    )


class DesignDiary(Base):
    """Append-only journal of every action taken on a design."""

    __tablename__ = "design_diary"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    design_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("designs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    entry_type: Mapped[str] = mapped_column(String(100), nullable=False)
    # entry_type examples: prompt, parameter_change, material_change, variant_switch,
    #                      optimize, export_step, note, ai_question, ai_explain

    snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Enough state to reconstruct-or-restore at this point in the design's history.

    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    design: Mapped[Design] = relationship("Design", back_populates="diary_entries")


class DesignFeedback(Base):
    """User feedback on a design: approve / reject / modify + reasons."""

    __tablename__ = "design_feedback"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    design_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("designs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5
    verdict: Mapped[str] = mapped_column(String(50), nullable=False)
    # verdict: approved | rejected | modified

    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Structured reasons for training future learning:
    # {"issues": ["fillet_too_small", "over_engineered"], "requested_changes": {...}}
    structured: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    design: Mapped[Design] = relationship("Design", back_populates="feedback")

    __table_args__ = (
        CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)",
            name="ck_design_feedback_rating_range",
        ),
        CheckConstraint(
            "verdict IN ('approved','rejected','modified')",
            name="ck_design_feedback_verdict_enum",
        ),
    )

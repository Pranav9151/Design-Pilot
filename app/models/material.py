"""
Material database — canonical + custom.

Canonical `materials` table is seeded from `app/data/materials.py`
(12 verified materials at v1.0; expansion to 200 is tracked as
separate sourcing work, NOT a sprint-1 fill-in).

`custom_materials` is for v1.5 (teams add their own).

CRITICAL: LLM code NEVER writes material properties. All numeric
values come from these tables or literally nowhere.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Material(Base):
    """Canonical material — seeded, read-only from app code."""

    __tablename__ = "materials"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Stable identifier used by engines (e.g. "aluminum_6061_t6"); immutable.
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    grade: Mapped[str] = mapped_column(String(50), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Mechanical — all SI units (MPa, kg/m³, etc.)
    youngs_modulus_mpa: Mapped[float] = mapped_column(Float, nullable=False)
    yield_strength_mpa: Mapped[float] = mapped_column(Float, nullable=False)
    ultimate_strength_mpa: Mapped[float] = mapped_column(Float, nullable=False)
    density_kg_m3: Mapped[float] = mapped_column(Float, nullable=False)
    poissons_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    elongation_percent: Mapped[float] = mapped_column(Float, nullable=False)

    # Thermal
    cte: Mapped[float] = mapped_column(Float, nullable=False)
    thermal_conductivity: Mapped[float] = mapped_column(Float, nullable=False)
    max_service_temp_c: Mapped[float] = mapped_column(Float, nullable=False)

    # Manufacturing
    machinability_rating: Mapped[float] = mapped_column(Float, nullable=False)

    # Cost
    cost_per_kg_usd: Mapped[float] = mapped_column(Float, nullable=False)

    # Provenance (REQUIRED; every number must be attributable)
    source: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # Sanity-check bounds on physical properties
        CheckConstraint("youngs_modulus_mpa > 0", name="ck_materials_e_positive"),
        CheckConstraint("yield_strength_mpa > 0", name="ck_materials_yield_positive"),
        CheckConstraint("density_kg_m3 > 0", name="ck_materials_density_positive"),
        CheckConstraint(
            "poissons_ratio > 0 AND poissons_ratio < 0.5",
            name="ck_materials_poisson_range",
        ),
        CheckConstraint(
            "machinability_rating >= 0 AND machinability_rating <= 100",
            name="ck_materials_machinability_range",
        ),
    )


class CustomMaterial(Base):
    """Team-scoped custom material (v1.5)."""

    __tablename__ = "custom_materials"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    team_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    grade: Mapped[str] = mapped_column(String(50), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)

    youngs_modulus_mpa: Mapped[float] = mapped_column(Float, nullable=False)
    yield_strength_mpa: Mapped[float] = mapped_column(Float, nullable=False)
    ultimate_strength_mpa: Mapped[float] = mapped_column(Float, nullable=False)
    density_kg_m3: Mapped[float] = mapped_column(Float, nullable=False)
    poissons_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    elongation_percent: Mapped[float] = mapped_column(Float, nullable=False)
    cte: Mapped[float] = mapped_column(Float, nullable=False)
    thermal_conductivity: Mapped[float] = mapped_column(Float, nullable=False)
    max_service_temp_c: Mapped[float] = mapped_column(Float, nullable=False)
    machinability_rating: Mapped[float] = mapped_column(Float, nullable=False)
    cost_per_kg_usd: Mapped[float] = mapped_column(Float, nullable=False)

    # Source is still required for team-provided materials.
    source: Mapped[str] = mapped_column(Text, nullable=False)

    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("team_id", "slug", name="uq_custom_materials_team_slug"),
    )

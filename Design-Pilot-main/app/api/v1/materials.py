"""
Materials API.

Week 1 deliverable: an authenticated curl to /api/v1/materials returns
the seeded rows. Materials are a read-only reference table; v1.5 adds
custom materials for teams.

No LLM touches this data — it is source-of-truth for stress analysis.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.iam import Permission
from app.iam.deps import CurrentUser, require_permission
from app.models import Material

router = APIRouter()


class MaterialRead(BaseModel):
    """Material row shape returned by the API."""

    id: str
    slug: str
    name: str
    grade: str
    category: str
    youngs_modulus_mpa: float
    yield_strength_mpa: float
    ultimate_strength_mpa: float
    density_kg_m3: float
    poissons_ratio: float
    elongation_percent: float
    cte: float
    thermal_conductivity: float
    max_service_temp_c: float
    machinability_rating: float
    cost_per_kg_usd: float
    source: str

    @classmethod
    def from_orm_model(cls, m: Material) -> "MaterialRead":
        return cls(
            id=str(m.id),
            slug=m.slug,
            name=m.name,
            grade=m.grade,
            category=m.category,
            youngs_modulus_mpa=m.youngs_modulus_mpa,
            yield_strength_mpa=m.yield_strength_mpa,
            ultimate_strength_mpa=m.ultimate_strength_mpa,
            density_kg_m3=m.density_kg_m3,
            poissons_ratio=m.poissons_ratio,
            elongation_percent=m.elongation_percent,
            cte=m.cte,
            thermal_conductivity=m.thermal_conductivity,
            max_service_temp_c=m.max_service_temp_c,
            machinability_rating=m.machinability_rating,
            cost_per_kg_usd=m.cost_per_kg_usd,
            source=m.source,
        )


class MaterialListResponse(BaseModel):
    count: int = Field(..., ge=0)
    items: list[MaterialRead]


class MaterialRecommendationRequest(BaseModel):
    load_n: float = Field(..., gt=0)
    process: Literal["cnc", "sheet_metal", "print"] = "cnc"
    environment: Literal["indoor", "outdoor", "marine", "high_temp"] = "indoor"
    prioritize: Literal["balanced", "strength", "weight", "cost"] = "balanced"
    top_k: int = Field(5, ge=1, le=5)


class MaterialRecommendationItem(BaseModel):
    slug: str
    name: str
    score: float
    tradeoff: str
    reasons: list[str]


class MaterialRecommendationResponse(BaseModel):
    items: list[MaterialRecommendationItem]


# ── Valid categories (mirrors POC dataset; keep tight until v1.5 custom) ──
_CATEGORIES = frozenset({
    "aluminum", "steel", "stainless", "titanium", "brass", "polymer",
})


@router.get(
    "",
    response_model=MaterialListResponse,
    summary="List materials",
)
async def list_materials(
    category: str | None = Query(None, description="Filter by category"),
    min_yield_mpa: float | None = Query(
        None, ge=0, description="Minimum yield strength (MPa)"
    ),
    sort_by: Literal["name", "yield_strength_mpa", "cost_per_kg_usd"] = Query("name"),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(require_permission(Permission.MATERIAL_VIEW)),
) -> MaterialListResponse:
    """List materials. All users with MATERIAL_VIEW permission can read."""
    if category and category not in _CATEGORIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid category. Must be one of: {sorted(_CATEGORIES)}",
        )

    stmt = select(Material)

    if category:
        stmt = stmt.where(Material.category == category)
    if min_yield_mpa is not None:
        stmt = stmt.where(Material.yield_strength_mpa >= min_yield_mpa)

    order_col = getattr(Material, sort_by)
    stmt = stmt.order_by(order_col).limit(limit).offset(offset)

    result = await db.execute(stmt)
    rows = result.scalars().all()

    return MaterialListResponse(
        count=len(rows),
        items=[MaterialRead.from_orm_model(m) for m in rows],
    )


@router.post(
    "/recommend",
    response_model=MaterialRecommendationResponse,
    summary="Recommend top materials for a use case",
)
async def recommend_materials(
    payload: MaterialRecommendationRequest,
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(require_permission(Permission.AI_RECOMMEND_MATERIAL)),
) -> MaterialRecommendationResponse:
    rows = (await db.execute(select(Material).order_by(Material.name.asc()))).scalars().all()
    ranked: list[MaterialRecommendationItem] = []
    for material in rows:
        score, reasons = _score_material(material, payload)
        ranked.append(MaterialRecommendationItem(
            slug=material.slug,
            name=material.name,
            score=round(score, 3),
            tradeoff=_material_tradeoff(material, payload.prioritize),
            reasons=reasons,
        ))
    ranked.sort(key=lambda item: item.score, reverse=True)
    return MaterialRecommendationResponse(items=ranked[:payload.top_k])


def _score_material(
    material: Material,
    payload: MaterialRecommendationRequest,
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    strength = material.yield_strength_mpa / max(material.density_kg_m3, 1.0)
    if payload.prioritize == "strength":
        score += material.yield_strength_mpa * 0.025
        reasons.append("High yield strength improves margin for the requested load.")
    elif payload.prioritize == "weight":
        score += strength * 1200
        reasons.append("Strong strength-to-weight ratio helps keep the bracket light.")
    elif payload.prioritize == "cost":
        score += max(0.0, 25 - material.cost_per_kg_usd) * 2.5
        reasons.append("Lower material cost helps with economical screening.")
    else:
        score += material.yield_strength_mpa * 0.015
        score += strength * 500
        score += max(0.0, 20 - material.cost_per_kg_usd)
        reasons.append("Balanced scoring weighs strength, mass, and raw material cost together.")

    if payload.process == "cnc":
        score += material.machinability_rating * 0.25
        reasons.append("Machinability is rewarded for CNC workflows.")

    if payload.environment == "marine":
        if material.category in {"stainless", "titanium", "polymer"}:
            score += 18
            reasons.append("Corrosion resistance suits marine exposure.")
        else:
            score -= 8
    elif payload.environment == "outdoor":
        if material.category in {"stainless", "aluminum", "titanium", "polymer"}:
            score += 10
            reasons.append("Outdoor corrosion resistance is favorable.")
    elif payload.environment == "high_temp":
        score += material.max_service_temp_c * 0.03
        reasons.append("Maximum service temperature supports elevated-temperature use.")

    if payload.load_n > 5000:
        score += material.yield_strength_mpa * 0.01
        reasons.append("Higher applied load shifts preference toward stronger alloys.")

    return score, reasons[:3]


def _material_tradeoff(material: Material, prioritize: str) -> str:
    if prioritize == "weight":
        return f"{material.name} is attractive when low mass matters more than absolute cost."
    if prioritize == "cost":
        return f"{material.name} screens well for cost, but final supplier pricing still needs confirmation."
    if prioritize == "strength":
        return f"{material.name} favors higher strength, with a likely mass or cost premium."
    return f"{material.name} offers a practical balance for first-pass bracket design."


@router.get(
    "/{slug}",
    response_model=MaterialRead,
    summary="Get a single material by slug",
)
async def get_material(
    slug: str,
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(require_permission(Permission.MATERIAL_VIEW)),
) -> MaterialRead:
    stmt = select(Material).where(Material.slug == slug)
    result = await db.execute(stmt)
    material = result.scalar_one_or_none()
    if material is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Material '{slug}' not found",
        )
    return MaterialRead.from_orm_model(material)

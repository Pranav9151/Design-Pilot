"""
Admin API — internal analytics and management endpoints.

Access requires the ADMIN permission (owner role or explicit grant).
Never expose these through a public-facing CDN — they should be behind
an internal network or at least a separate secret header in production.

Endpoints:
  GET  /api/v1/admin/stats          Platform-level stats
  GET  /api/v1/admin/users          User list with plan info
  POST /api/v1/admin/users/{id}/plan   Force-set a user's plan
"""
from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.iam import Permission
from app.iam.deps import CurrentUser, require_permission
from app.models.design import Design
from app.models.user import User

router = APIRouter()
logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────


class PlatformStats(BaseModel):
    total_users: int
    total_designs: int
    designs_last_30_days: int
    designs_by_status: dict[str, int]
    users_by_plan: dict[str, int]
    materials_count: int


class AdminUserRow(BaseModel):
    id: str
    email: str
    plan: str
    design_count: int
    created_at: str


class SetPlanRequest(BaseModel):
    plan: str  # free | pro | team


# ─────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────


@router.get("/stats", response_model=PlatformStats)
async def platform_stats(
    current_user: CurrentUser = Depends(require_permission(Permission.TEAM_BILLING_EDIT)),
    db: AsyncSession = Depends(get_db),
) -> PlatformStats:
    """Aggregate platform statistics. Requires team-admin or owner permission."""
    from datetime import datetime, timedelta, timezone
    from app.models.material import Material

    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)

    total_users = (await db.execute(select(func.count()).select_from(User))).scalar_one()
    total_designs = (await db.execute(select(func.count()).select_from(Design))).scalar_one()
    recent_designs = (await db.execute(
        select(func.count()).select_from(Design).where(Design.created_at >= thirty_days_ago)
    )).scalar_one()
    materials_count = (await db.execute(select(func.count()).select_from(Material))).scalar_one()

    # Designs by status
    status_rows = (await db.execute(
        select(Design.status, func.count().label("n"))
        .group_by(Design.status)
    )).all()
    designs_by_status = {row.status: row.n for row in status_rows}

    # Users by plan
    plan_rows = (await db.execute(
        select(User.plan, func.count().label("n"))
        .group_by(User.plan)
    )).all()
    users_by_plan = {(row.plan or "free"): row.n for row in plan_rows}

    return PlatformStats(
        total_users=total_users,
        total_designs=total_designs,
        designs_last_30_days=recent_designs,
        designs_by_status=designs_by_status,
        users_by_plan=users_by_plan,
        materials_count=materials_count,
    )


@router.get("/users", response_model=list[AdminUserRow])
async def list_users(
    limit: int = 50,
    offset: int = 0,
    current_user: CurrentUser = Depends(require_permission(Permission.TEAM_BILLING_EDIT)),
    db: AsyncSession = Depends(get_db),
) -> list[AdminUserRow]:
    """List all users with their plan and design count."""
    limit = max(1, min(200, limit))

    rows = (await db.execute(
        select(
            User.id,
            User.email,
            User.plan,
            User.created_at,
            func.count(Design.id).label("design_count"),
        )
        .outerjoin(Design, Design.owner_id == User.id)
        .group_by(User.id, User.email, User.plan, User.created_at)
        .order_by(User.created_at.desc())
        .limit(limit)
        .offset(offset)
    )).all()

    return [
        AdminUserRow(
            id=str(row.id),
            email=row.email or "",
            plan=row.plan or "free",
            design_count=row.design_count,
            created_at=row.created_at.isoformat() if row.created_at else "",
        )
        for row in rows
    ]


@router.post("/users/{user_id}/plan", response_model=dict)
async def set_user_plan(
    user_id: UUID,
    payload: SetPlanRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.TEAM_BILLING_EDIT)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Force-set a user's plan (admin override, e.g. for support or trials)."""
    valid_plans = {"free", "pro", "team"}
    if payload.plan not in valid_plans:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"plan must be one of: {valid_plans}",
        )

    result = await db.execute(
        update(User).where(User.id == user_id).values(plan=payload.plan)
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="user not found")

    await db.commit()
    logger.info("admin_set_user_plan", admin=str(current_user.id), target=str(user_id), plan=payload.plan)
    return {"user_id": str(user_id), "plan": payload.plan}

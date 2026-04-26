"""
Designs API.

    POST   /api/v1/designs          — generate a new design from a prompt
    GET    /api/v1/designs          — list the caller's designs (RLS-filtered)
    GET    /api/v1/designs/:id      — fetch one design (RLS-filtered)
    DELETE /api/v1/designs/:id      — soft-delete (sets status='archived')

POST is the hot path: it runs `GenerationPipeline` end-to-end, which
takes ~30-60 seconds in production (2 LLM calls + 3 Docker sandbox runs).
The endpoint is intentionally synchronous in v1.0 for simplicity; Week 4
adds SSE streaming so the UI can show live progress.

Rate limiting + permission checks happen BEFORE the pipeline starts, so
an over-quota request costs zero tokens.

**Dependency injection:** `get_pipeline()` is a FastAPI dependency so
tests can `app.dependency_overrides[get_pipeline] = ...` to swap in a
fake pipeline with stubbed LLM + sandbox. This matches how `get_db` and
`redis_dependency` are overridden elsewhere.
"""
from __future__ import annotations

from copy import deepcopy
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import audit_service
from app.core.db import get_db
from app.core.rate_limit import RateLimiter
from app.core.redis_client import redis_dependency
from app.engines.dfm import DFMEngine
from app.iam import Permission
from app.iam.deps import CurrentUser, require_permission
from app.models import Material
from app.models.design import Design, DesignDiary
from app.models.user import User
from app.services.llm_client import LLMError
from app.services.pipeline import (
    GenerationPipeline,
    PipelineError,
    VariantOutcome,
    VariantSpec,
    _compute_analytics,
    _emit_cadquery_code,
    _pick_best_variant,
    _variant_outcome_to_dict,
)
from app.services.sandbox import SandboxResult
from app.services.triple_lock import triple_lock

router = APIRouter()


def get_pipeline() -> GenerationPipeline:
    """FastAPI dependency — override in tests to inject fakes.

    Production returns a real pipeline with the module-level singletons
    (real Anthropic client, real Docker sandbox, real R2 storage).
    """
    return GenerationPipeline()


# ═════════════════════════════════════════════════════════════════════
# Request / response shapes
# ═════════════════════════════════════════════════════════════════════


class GenerateDesignRequest(BaseModel):
    prompt: str = Field(..., min_length=10, max_length=4000)


class DesignSummary(BaseModel):
    """List-view shape — not the full payload."""

    id: UUID
    name: str | None
    status: str
    prompt: str | None
    confidence_score: float | None
    confidence_band: str | None = None
    recommended_variant: str | None = None
    step_url: str | None
    glb_url: str | None
    created_at: str

    @classmethod
    def from_row(cls, d: Design) -> "DesignSummary":
        band = None
        recommended = None
        if d.parameters:
            band = (d.parameters.get("variants") or [{}])[0].get(
                "triple_lock", {}
            ).get("band")
            recommended = d.parameters.get("recommended")
        return cls(
            id=d.id,
            name=d.name,
            status=d.status,
            prompt=(d.prompt[:200] if d.prompt else None),
            confidence_score=d.confidence_score,
            confidence_band=band,
            recommended_variant=recommended,
            step_url=d.step_url,
            glb_url=d.glb_url,
            created_at=d.created_at.isoformat(),
        )


class DesignDetail(BaseModel):
    """Full shape returned from POST and GET :id."""

    id: UUID
    name: str | None
    status: str
    prompt: str | None
    part_type: str
    cadquery_code: str | None
    parameters: dict
    step_url: str | None
    glb_url: str | None
    confidence_score: float | None
    confidence_explanation: str | None
    lock1_results: dict | None
    lock2_results: dict | None
    lock3_results: dict | None
    simulation: dict | None
    dfm: dict | None
    cost: dict | None
    assumptions: list[str]
    material_id: UUID | None
    created_at: str

    @classmethod
    def from_row(cls, d: Design) -> "DesignDetail":
        return cls(
            id=d.id,
            name=d.name,
            status=d.status,
            prompt=d.prompt,
            part_type=d.part_type,
            cadquery_code=d.cadquery_code,
            parameters=dict(d.parameters or {}),
            step_url=d.step_url,
            glb_url=d.glb_url,
            confidence_score=d.confidence_score,
            confidence_explanation=d.confidence_explanation,
            lock1_results=d.lock1_results,
            lock2_results=d.lock2_results,
            lock3_results=d.lock3_results,
            simulation=d.simulation,
            dfm=d.dfm,
            cost=d.cost,
            assumptions=list(d.assumptions or []),
            material_id=d.material_id,
            created_at=d.created_at.isoformat(),
        )


# ═════════════════════════════════════════════════════════════════════
# POST /api/v1/designs
# ═════════════════════════════════════════════════════════════════════


@router.post(
    "",
    response_model=DesignDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a new design from a natural-language prompt",
)
async def create_design(
    payload: GenerateDesignRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.DESIGN_CREATE)),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(redis_dependency),
    pipeline: GenerationPipeline = Depends(get_pipeline),
) -> DesignDetail:
    """
    Run the full generation pipeline and return the resulting design.

    Order of operations (fail-fast at each step):
        1. JWT auth + permission check (`DESIGN_CREATE`).
        2. Rate-limit check (5/month free, 500/month pro).
        3. Pipeline: parse → 3 variants → sandbox → triple-lock → persist.
        4. Commit.

    A rate-limited request costs zero LLM tokens. Any error past step 3
    triggers a 500 with the run_id for log correlation (pipeline log lines
    include the same run_id).
    """
    # Step 2: rate limit — look up the user's plan from DB
    from app.models.user import User
    user_row = (await db.execute(
        select(User).where(User.id == current_user.id)
    )).scalar_one_or_none()
    plan = (user_row.plan if user_row else "free") or "free"

    limiter = RateLimiter(redis_client=redis)
    decision = await limiter.check(
        user_id=current_user.id,
        plan=plan,
        action="design.create",
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "rate_limited",
                "reason": decision.reason,
                "current": decision.current,
                "limit": decision.limit,
                "resets_at_utc": decision.resets_at_utc.isoformat(),
            },
            headers={
                "X-RateLimit-Limit": str(decision.limit),
                "X-RateLimit-Remaining": str(decision.remaining),
                "X-RateLimit-Reset": decision.resets_at_utc.isoformat(),
            },
        )

    # Step 3: run the pipeline
    try:
        result = await pipeline.run(
            prompt=payload.prompt,
            user_id=current_user.id,
            session=db,
        )
    except PipelineError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "pipeline_error", "message": str(exc)},
        )
    except LLMError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": "llm_error", "message": str(exc)},
        )

    await db.commit()

    # Re-read the design from DB so the response reflects what's actually
    # persisted (server-side defaults, timestamps, etc.)
    design = (await db.execute(
        select(Design).where(Design.id == result.design_id)
    )).scalar_one()

    return DesignDetail.from_row(design)


# ═════════════════════════════════════════════════════════════════════
# GET /api/v1/designs
# ═════════════════════════════════════════════════════════════════════


@router.get(
    "",
    response_model=list[DesignSummary],
    summary="List the current user's designs",
)
async def list_designs(
    current_user: CurrentUser = Depends(require_permission(Permission.DESIGN_VIEW_OWN)),
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
) -> list[DesignSummary]:
    """Returns designs owned by the caller.

    Defense in depth:
      - RLS enforces `owner_id = current_user_id()` at the DB level (prod).
      - Application-layer WHERE filter enforces it here too. This is the
        working defense in dev where the DB role is superuser and bypasses
        RLS. It is also the right pattern for any future code path that
        doesn't set `app.current_user_id` on the session (e.g. a background
        worker).
    """
    limit = max(1, min(200, limit))
    offset = max(0, offset)

    stmt = (
        select(Design)
        .where(Design.owner_id == current_user.id)
        .order_by(Design.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [DesignSummary.from_row(d) for d in rows]


# ═════════════════════════════════════════════════════════════════════
# GET /api/v1/designs/:id
# ═════════════════════════════════════════════════════════════════════


@router.get(
    "/{design_id}",
    response_model=DesignDetail,
    summary="Fetch a single design by id",
)
async def get_design(
    design_id: UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.DESIGN_VIEW_OWN)),
    db: AsyncSession = Depends(get_db),
) -> DesignDetail:
    design = (await db.execute(
        select(Design).where(
            Design.id == design_id,
            Design.owner_id == current_user.id,   # app-layer RLS; see list_designs
        )
    )).scalar_one_or_none()

    if design is None:
        # 404 (not 403) on cross-user access so we don't leak design existence.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="design not found")

    return DesignDetail.from_row(design)


# ═════════════════════════════════════════════════════════════════════
# DELETE /api/v1/designs/:id   (soft-delete)
# ═════════════════════════════════════════════════════════════════════


@router.delete(
    "/{design_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive a design (soft-delete)",
)
async def delete_design(
    design_id: UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.DESIGN_DELETE_OWN)),
    db: AsyncSession = Depends(get_db),
) -> None:
    design = (await db.execute(
        select(Design).where(
            Design.id == design_id,
            Design.owner_id == current_user.id,
        )
    )).scalar_one_or_none()
    if design is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="design not found")

    design.status = "archived"
    # Diary entry for audit trail
    db.add(DesignDiary(
        design_id=design.id,
        user_id=current_user.id,
        entry_type="archived",
        note="Design archived by user",
    ))
    await db.commit()


# ═════════════════════════════════════════════════════════════════════
# GET /api/v1/designs/:id/diary   — Design Diary entries
# ═════════════════════════════════════════════════════════════════════


class DiaryEntryOut(BaseModel):
    """One timestamped design decision record."""

    id: UUID
    entry_type: str
    note: str | None
    snapshot: dict
    created_at: str

    @classmethod
    def from_row(cls, d: DesignDiary) -> "DiaryEntryOut":
        return cls(
            id=d.id,
            entry_type=d.entry_type,
            note=d.note,
            snapshot=dict(d.snapshot or {}),
            created_at=d.created_at.isoformat(),
        )


@router.get(
    "/{design_id}/diary",
    response_model=list[DiaryEntryOut],
    summary="Get Design Diary entries for a design",
)
async def get_design_diary(
    design_id: UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.DESIGN_VIEW_OWN)),
    db: AsyncSession = Depends(get_db),
    limit: int = 100,
) -> list[DiaryEntryOut]:
    """
    Returns all auto-captured Design Diary entries for a design,
    in chronological order. Every generation step, parameter change,
    and user action is recorded here.

    Defense: the parent design is first fetched with the owner_id check
    (same pattern as GET /:id) so a cross-user request gets 404.
    """
    # Confirm ownership (app-layer RLS)
    design = (await db.execute(
        select(Design).where(
            Design.id == design_id,
            Design.owner_id == current_user.id,
        )
    )).scalar_one_or_none()
    if design is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="design not found")

    limit = max(1, min(500, limit))
    entries = (await db.execute(
        select(DesignDiary)
        .where(DesignDiary.design_id == design_id)
        .order_by(DesignDiary.created_at.asc())
        .limit(limit)
    )).scalars().all()

    return [DiaryEntryOut.from_row(e) for e in entries]


class ParameterPatchRequest(BaseModel):
    base_width_mm: float | None = Field(None, gt=10, lt=500)
    base_depth_mm: float | None = Field(None, gt=10, lt=500)
    base_thickness_mm: float | None = Field(None, gt=1, lt=50)
    wall_height_mm: float | None = Field(None, gt=10, lt=500)
    wall_thickness_mm: float | None = Field(None, gt=1, lt=50)
    fillet_radius_mm: float | None = Field(None, gt=0.1, lt=50)
    hole_diameter_mm: float | None = Field(None, gt=1, lt=100)
    hole_spacing_x_mm: float | None = Field(None, gt=1, lt=500)
    hole_spacing_y_mm: float | None = Field(None, gt=1, lt=500)
    recommended_variant: str | None = Field(None, pattern="^[ABC]$")


class ExplainResponse(BaseModel):
    summary: str


class WhyNotResponse(BaseModel):
    recommended_variant: str
    why_recommended: str
    why_not_a: str
    why_not_b: str
    why_not_c: str


class SimilarDesignItem(BaseModel):
    id: UUID
    name: str | None
    prompt: str | None
    similarity_score: float
    recommended_variant: str | None
    created_at: str


class SimilarDesignsResponse(BaseModel):
    items: list[SimilarDesignItem]


class SeniorEngineerQuestionsResponse(BaseModel):
    questions: list[str]


class OptimizeRequest(BaseModel):
    goal: str = Field(..., min_length=10, max_length=500)


class OptimizeResponse(BaseModel):
    goal: str
    recommended_variant: str
    design: DesignDetail


@router.patch(
    "/{design_id}/parameters",
    response_model=DesignDetail,
    summary="Update dimensions and re-run deterministic analysis",
)
async def patch_design_parameters(
    design_id: UUID,
    payload: ParameterPatchRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.DESIGN_EDIT_OWN)),
    db: AsyncSession = Depends(get_db),
    pipeline: GenerationPipeline = Depends(get_pipeline),
) -> DesignDetail:
    design = await _get_owned_design(db, design_id, current_user.id)
    material = await _get_design_material(db, design)

    variants = await _rebuild_variants_for_design(
        design=design,
        payload=payload.model_dump(exclude_none=True),
        material=material,
        pipeline=pipeline,
    )
    recommended = payload.recommended_variant or _pick_best_variant(variants)
    _apply_variant_results_to_design(design, variants, recommended, material.id)
    design.version += 1

    db.add(DesignDiary(
        design_id=design.id,
        user_id=current_user.id,
        entry_type="parameter_change",
        note="Parameters updated and analysis refreshed.",
        snapshot={"changes": payload.model_dump(exclude_none=True), "recommended": recommended},
    ))
    await audit_service.log(
        session=db,
        actor_user_id=current_user.id,
        action="design.parameters.patch",
        resource_type="design",
        resource_id=design.id,
        metadata={"changes": payload.model_dump(exclude_none=True), "recommended": recommended},
    )
    await db.commit()
    await db.refresh(design)
    return DesignDetail.from_row(design)


@router.post(
    "/{design_id}/explain",
    response_model=ExplainResponse,
    summary="Explain a design in manager-friendly language",
)
async def explain_design(
    design_id: UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.AI_EXPLAIN)),
    db: AsyncSession = Depends(get_db),
) -> ExplainResponse:
    design = await _get_owned_design(db, design_id, current_user.id)
    qa = _qa_blob(design)
    recommended = (design.parameters or {}).get("recommended", "C")
    variant = _variant_by_label(design, recommended)
    material = await _get_design_material(db, design)
    summary = _manager_summary(design, variant, material.name if material else "selected material", qa)
    return ExplainResponse(summary=summary)


@router.get(
    "/{design_id}/why-not",
    response_model=WhyNotResponse,
    summary="Expose recommendation reasoning for the three variants",
)
async def why_not_design(
    design_id: UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.DESIGN_VIEW_OWN)),
    db: AsyncSession = Depends(get_db),
) -> WhyNotResponse:
    design = await _get_owned_design(db, design_id, current_user.id)
    qa = _qa_blob(design)
    recommended = (design.parameters or {}).get("recommended", "C")
    return WhyNotResponse(
        recommended_variant=recommended,
        why_recommended=qa.get("why_recommended") or "This variant best balances safety factor, manufacturability, and cost for the current prompt.",
        why_not_a=qa.get("why_not_a") or _fallback_why_not(design, "A", recommended),
        why_not_b=qa.get("why_not_b") or _fallback_why_not(design, "B", recommended),
        why_not_c=qa.get("why_not_c") or _fallback_why_not(design, "C", recommended),
    )


@router.get(
    "/{design_id}/similar",
    response_model=SimilarDesignsResponse,
    summary="Find similar designs owned by the current user",
)
async def similar_designs(
    design_id: UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.DESIGN_VIEW_OWN)),
    db: AsyncSession = Depends(get_db),
    limit: int = 5,
) -> SimilarDesignsResponse:
    design = await _get_owned_design(db, design_id, current_user.id)
    limit = max(1, min(10, limit))
    rows = (await db.execute(
        select(Design).where(
            Design.owner_id == current_user.id,
            Design.id != design.id,
            Design.part_type == design.part_type,
            Design.status != "archived",
        )
        .order_by(Design.created_at.desc())
        .limit(50)
    )).scalars().all()
    items = [
        SimilarDesignItem(
            id=row.id,
            name=row.name,
            prompt=row.prompt,
            similarity_score=_similarity_score(design, row),
            recommended_variant=(row.parameters or {}).get("recommended"),
            created_at=row.created_at.isoformat(),
        )
        for row in rows
    ]
    items.sort(key=lambda item: item.similarity_score, reverse=True)
    return SimilarDesignsResponse(items=items[:limit])


@router.get(
    "/{design_id}/questions",
    response_model=SeniorEngineerQuestionsResponse,
    summary="Show senior-engineer review questions for a design",
)
async def design_questions(
    design_id: UUID,
    current_user: CurrentUser = Depends(require_permission(Permission.DESIGN_VIEW_OWN)),
    db: AsyncSession = Depends(get_db),
) -> SeniorEngineerQuestionsResponse:
    design = await _get_owned_design(db, design_id, current_user.id)
    qa = _qa_blob(design)
    questions = qa.get("senior_engineer_questions") or _fallback_questions(design)
    return SeniorEngineerQuestionsResponse(questions=questions[:5])


@router.post(
    "/{design_id}/optimize",
    response_model=OptimizeResponse,
    summary="Optimize an existing design against a goal",
)
async def optimize_design(
    design_id: UUID,
    payload: OptimizeRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.AI_OPTIMIZE)),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(redis_dependency),
    pipeline: GenerationPipeline = Depends(get_pipeline),
) -> OptimizeResponse:
    design = await _get_owned_design(db, design_id, current_user.id)
    user_row = (await db.execute(
        select(User).where(User.id == current_user.id)
    )).scalar_one_or_none()
    plan = (user_row.plan if user_row else "free") or "free"

    limiter = RateLimiter(redis_client=redis)
    decision = await limiter.check(user_id=current_user.id, plan=plan, action="ai.optimize")
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": "rate_limited", "reason": decision.reason, "current": decision.current, "limit": decision.limit},
        )

    material = await _get_design_material(db, design)
    target_variant = _variant_by_label(design, (design.parameters or {}).get("recommended", "C"))
    base_spec = VariantSpec(**target_variant["spec"]) if target_variant else VariantSpec(**((design.parameters or {}).get("variants") or [])[0]["spec"])
    goal_lower = payload.goal.lower()

    candidate_payloads = [
        {},
        {"wall_thickness_mm": base_spec.wall_thickness_mm * 0.9, "base_thickness_mm": base_spec.base_thickness_mm * 0.9},
        {"wall_thickness_mm": base_spec.wall_thickness_mm * 1.1, "base_thickness_mm": base_spec.base_thickness_mm * 1.1},
        {"base_width_mm": base_spec.base_width_mm * 0.92, "base_depth_mm": base_spec.base_depth_mm * 0.92},
        {"base_width_mm": base_spec.base_width_mm * 1.08, "base_depth_mm": base_spec.base_depth_mm * 1.08},
    ]
    candidates: list[tuple[float, list[VariantOutcome]]] = []
    for candidate in candidate_payloads:
        variants = await _rebuild_variants_for_design(
            design=design,
            payload=candidate,
            material=material,
            pipeline=pipeline,
        )
        score = _optimize_score(goal_lower, variants)
        candidates.append((score, variants))
    candidates.sort(key=lambda item: item[0], reverse=True)
    best_variants = candidates[0][1]
    recommended = _pick_best_variant(best_variants)
    _apply_variant_results_to_design(design, best_variants, recommended, material.id)
    design.version += 1

    db.add(DesignDiary(
        design_id=design.id,
        user_id=current_user.id,
        entry_type="optimize",
        note=f"Optimized design for goal: {payload.goal[:200]}",
        snapshot={"goal": payload.goal, "recommended": recommended},
    ))
    await audit_service.log(
        session=db,
        actor_user_id=current_user.id,
        action="design.optimize",
        resource_type="design",
        resource_id=design.id,
        metadata={"goal": payload.goal, "recommended": recommended},
    )
    await db.commit()
    await db.refresh(design)
    return OptimizeResponse(goal=payload.goal, recommended_variant=recommended, design=DesignDetail.from_row(design))


async def _get_owned_design(db: AsyncSession, design_id: UUID, user_id: UUID) -> Design:
    design = (await db.execute(
        select(Design).where(Design.id == design_id, Design.owner_id == user_id)
    )).scalar_one_or_none()
    if design is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="design not found")
    return design


async def _get_design_material(db: AsyncSession, design: Design) -> Material:
    material = None
    if design.material_id is not None:
        material = (await db.execute(select(Material).where(Material.id == design.material_id))).scalar_one_or_none()
    if material is None:
        material_slug = ((design.parameters or {}).get("request") or {}).get("material_slug")
        if material_slug:
            material = (await db.execute(select(Material).where(Material.slug == material_slug))).scalar_one_or_none()
    if material is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="design material not found")
    return material


async def _rebuild_variants_for_design(
    *,
    design: Design,
    payload: dict,
    material: Material,
    pipeline: GenerationPipeline,
) -> list[VariantOutcome]:
    variants_data = (design.parameters or {}).get("variants") or []
    if not variants_data:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="design has no variants")

    request = ((design.parameters or {}).get("request") or {})
    load = request.get("load") or {}
    out: list[VariantOutcome] = []
    for item in variants_data:
        spec_data = deepcopy(item["spec"])
        for key, value in payload.items():
            if key in spec_data:
                spec_data[key] = value
        spec = VariantSpec(**spec_data)
        code = _emit_cadquery_code(spec)
        sb = pipeline.sandbox.run(code, skip_ast_check=False, run_id=f"patch-{design.id}-{spec.label}")
        volume_mm3 = sb.metrics.get("volume_mm3") if sb.ok else None
        stress_mpa, sf, mass_kg, cost_usd = _compute_analytics(
            spec=spec,
            load_n=float(load.get("magnitude_n") or 0.0),
            lever_arm_mm=float(load.get("lever_arm_mm") or 0.0),
            material=material,
            volume_mm3=volume_mm3,
        )
        tl = triple_lock.verify(lock1_value=stress_mpa)
        dfm_issues = _dfm_issues(spec, material)
        step_url = item.get("step_url")
        glb_url = item.get("glb_url")
        if sb.ok and sb.step_path:
            try:
                loc = await pipeline.storage.upload_design_files(design_id=design.id, step_path=sb.step_path, glb_path=sb.glb_path)
                step_url = loc.step.url
                glb_url = loc.glb.url if loc.glb else None
            except Exception:
                pass
        out.append(VariantOutcome(
            spec=spec,
            cadquery_code=code,
            sandbox=sb if isinstance(sb, SandboxResult) else SandboxResult(ok=False, stage="sandbox", error="sandbox failed"),
            analytical_stress_mpa=stress_mpa,
            mass_kg=mass_kg,
            cost_usd=cost_usd,
            safety_factor=sf,
            dfm_issues=dfm_issues,
            triple_lock=tl,
            step_url=step_url,
            glb_url=glb_url,
        ))
    return out


def _dfm_issues(spec: VariantSpec, material: Material) -> list[str]:
    try:
        result = DFMEngine().check_cnc(
            wall_thicknesses_mm=[spec.wall_thickness_mm, spec.base_thickness_mm],
            fillet_radii_mm=[spec.fillet_radius_mm],
            hole_diameters_mm=[spec.hole_diameter_mm],
            hole_depths_mm=[spec.base_thickness_mm],
            pocket_depths_mm=[],
            material_category=material.category,
            wall_heights_mm=[spec.wall_height_mm],
        )
        return [getattr(issue, "message", str(issue)) for issue in getattr(result, "issues", []) or []]
    except Exception:
        return []


def _apply_variant_results_to_design(design: Design, variants: list[VariantOutcome], recommended: str, material_id: UUID) -> None:
    chosen = next((o for o in variants if o.spec.label == recommended), variants[0])
    design.status = "analyzed" if chosen.sandbox.ok else "failed"
    design.cadquery_code = chosen.cadquery_code
    design.parameters = {
        **(design.parameters or {}),
        "recommended": recommended,
        "variants": [_variant_outcome_to_dict(o) for o in variants],
    }
    design.step_url = chosen.step_url
    design.glb_url = chosen.glb_url
    design.lock1_results = {
        "value_mpa": chosen.analytical_stress_mpa,
        "status": chosen.triple_lock.lock1.status.value,
    } if chosen.triple_lock else None
    design.lock2_results = {
        "status": chosen.triple_lock.lock2.status.value,
        "note": chosen.triple_lock.lock2.note,
    } if chosen.triple_lock else None
    design.lock3_results = {
        "status": chosen.triple_lock.lock3.status.value,
        "note": chosen.triple_lock.lock3.note,
    } if chosen.triple_lock else None
    design.confidence_score = chosen.triple_lock.confidence_score if chosen.triple_lock else None
    design.confidence_explanation = chosen.triple_lock.explanation if chosen.triple_lock else None
    design.material_id = material_id
    design.simulation = {
        "max_stress_mpa": chosen.analytical_stress_mpa,
        "safety_factor": chosen.safety_factor,
        "method": "analytical_bending_stress_shigleys_eq3_24",
    }
    design.dfm = {"issues": chosen.dfm_issues}
    design.cost = {"total_usd": chosen.cost_usd, "method": "v1_material_x3_markup"}


def _qa_blob(design: Design) -> dict:
    return dict(((design.parameters or {}).get("qa") or {}))


def _variant_by_label(design: Design, label: str) -> dict | None:
    for variant in (design.parameters or {}).get("variants") or []:
        if variant.get("spec", {}).get("label") == label:
            return variant
    return None


def _manager_summary(design: Design, variant: dict | None, material_name: str, qa: dict) -> str:
    if variant is None:
        return "This design needs manual review because the recommended variant details are missing."
    stress = variant.get("analytical_stress_mpa")
    sf = variant.get("safety_factor")
    cost = variant.get("cost_usd")
    mass = variant.get("mass_kg")
    why = qa.get("why_recommended") or "It offered the cleanest balance between safety, manufacturability, and cost."
    return (
        f"This bracket design was generated from the prompt '{(design.prompt or '').strip()}'. "
        f"The recommended option is Variant {variant['spec']['label']} in {material_name}, "
        f"with an estimated safety factor of {sf:.2f}, peak stress of {stress:.1f} MPa, "
        f"mass of {mass:.3f} kg, and estimated cost of ${cost:.2f}. "
        f"{why} The result is backed by the deterministic formula engine and the Triple-Lock confidence check."
    )


def _fallback_why_not(design: Design, label: str, recommended: str) -> str:
    if label == recommended:
        return f"Variant {label} is the recommendation."
    variant = _variant_by_label(design, label)
    if variant is None:
        return f"Variant {label} is unavailable."
    issues = variant.get("dfm_issues") or []
    if issues:
        return f"Variant {label} was not recommended because it introduces DFM concerns: {issues[0]}"
    sf = variant.get("safety_factor") or 0
    rec = _variant_by_label(design, recommended)
    rec_sf = (rec or {}).get("safety_factor") or 0
    if sf < rec_sf:
        return f"Variant {label} was not recommended because its safety factor is lower than Variant {recommended}."
    return f"Variant {label} was not recommended because it offers a weaker cost-to-performance trade-off than Variant {recommended}."


def _fallback_questions(design: Design) -> list[str]:
    request = ((design.parameters or {}).get("request") or {})
    load = request.get("load") or {}
    questions = [
        "Have you confirmed the support structure and bolt pattern can carry the reaction load without local deformation?",
        "Does the operating environment introduce corrosion, vibration, or thermal cycling that should change the material choice or safety factor?",
        "Do you need access clearances for tools, washers, or assembly tolerances around the holes and fillets?",
    ]
    if (load.get("lever_arm_mm") or 0) > 120:
        questions.append("Should the long lever arm trigger a gusset or local stiffening review to control deflection?")
    return questions


def _similarity_score(a: Design, b: Design) -> float:
    score = 0.0
    if a.material_id and a.material_id == b.material_id:
        score += 0.35
    a_prompt = set((a.prompt or "").lower().split())
    b_prompt = set((b.prompt or "").lower().split())
    if a_prompt and b_prompt:
        score += 0.45 * (len(a_prompt & b_prompt) / len(a_prompt | b_prompt))
    a_req = ((a.parameters or {}).get("request") or {})
    b_req = ((b.parameters or {}).get("request") or {})
    if a_req.get("process") and a_req.get("process") == b_req.get("process"):
        score += 0.1
    if (a_req.get("load") or {}).get("type") == (b_req.get("load") or {}).get("type"):
        score += 0.1
    return round(score, 4)


def _optimize_score(goal: str, variants: list[VariantOutcome]) -> float:
    chosen = next((v for v in variants if v.sandbox.ok), variants[0])
    sf = chosen.safety_factor or 0.0
    mass = chosen.mass_kg or 0.0
    cost = chosen.cost_usd or 0.0
    if "weight" in goal or "lighter" in goal or "mass" in goal:
        return sf * 10 - mass * 100 - cost
    if "cost" in goal or "cheap" in goal:
        return sf * 10 - cost * 5 - mass * 20
    if "strength" in goal or "safety" in goal:
        return sf * 20 - cost - mass * 10
    return sf * 12 - cost * 2 - mass * 25

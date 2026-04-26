"""
SSE streaming endpoint for design generation.

Why SSE over WebSockets?
  - Generation takes 30–60s in production (2 LLM calls + 3 Docker sandbox runs).
  - Engineers need live progress; a silent spinner for a minute kills UX.
  - SSE is simpler than WebSockets (unidirectional, works through proxies,
    no upgrade handshake, native EventSource in browsers, no library on
    the frontend required).

Event stream protocol (each event is JSON, newline-delimited):

    event: progress
    data: {"stage": "parsing_prompt", "message": "Understanding your design...", "pct": 5}

    event: progress
    data: {"stage": "variant_a_sandbox", "message": "Building variant A (Lightest)...", "pct": 30}

    event: progress
    data: {"stage": "variant_b_sandbox", "message": "Building variant B (Strongest)...", "pct": 50}

    event: progress
    data: {"stage": "variant_c_sandbox", "message": "Building variant C (Economical)...", "pct": 70}

    event: progress
    data: {"stage": "qa_synthesis", "message": "Writing engineering summary...", "pct": 90}

    event: complete
    data: { ...DesignDetail... }

    OR on error:
    event: error
    data: {"code": "pipeline_error", "message": "..."}

Reconnect / idempotency:
  The SSE endpoint is POST-based (not REST-idempotent). If the client
  reconnects after a disconnect, it gets a fresh run. Week 5 will add a
  `design_id` reservation step so reconnects can resume an in-progress run.

Rate limiting:
  Identical to the non-streaming POST. The rate-limit check happens before
  the stream opens, so a 429 response is returned as a regular HTTP error
  (not via the event stream) — this is intentional so the frontend sees a
  normal HTTP status and can render the upgrade prompt.
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.rate_limit import RateLimiter
from app.core.redis_client import redis_dependency
from app.iam import Permission
from app.iam.deps import CurrentUser, require_permission
from app.models.design import Design
from app.models.user import User
from app.services.llm_client import LLMError
from app.services.pipeline import (
    GenerationPipeline,
    PipelineError,
    _derive_three_variants,
    _emit_cadquery_code,
    _compute_analytics,
    _load_materials_by_slug,
    _design_title,
    _variant_outcome_to_dict,
    _pick_best_variant,
    VariantSpec,
    VariantOutcome,
)
from app.services.sandbox import SandboxResult

router = APIRouter()
logger = structlog.get_logger(__name__)

# ─── Re-use the pipeline dependency from designs.py ──────────────────
from app.api.v1.designs import get_pipeline, DesignDetail  # noqa: E402


# ═════════════════════════════════════════════════════════════════════
# SSE helpers
# ═════════════════════════════════════════════════════════════════════

def _sse(event: str, data: dict) -> str:
    """Format a single SSE message."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _progress(stage: str, message: str, pct: int) -> str:
    return _sse("progress", {"stage": stage, "message": message, "pct": pct})


# ═════════════════════════════════════════════════════════════════════
# Request schema
# ═════════════════════════════════════════════════════════════════════

class StreamDesignRequest(BaseModel):
    prompt: str = Field(..., min_length=10, max_length=4000)


# ═════════════════════════════════════════════════════════════════════
# Streaming generator
# ═════════════════════════════════════════════════════════════════════

async def _stream_design_generation(
    *,
    prompt: str,
    user_id: UUID,
    db: AsyncSession,
    pipeline: GenerationPipeline,
) -> AsyncIterator[str]:
    """
    Async generator that yields SSE strings throughout the pipeline run.

    We re-implement the pipeline orchestration here (instead of calling
    pipeline.run()) so we can emit progress events between each step.
    The core logic is identical to GenerationPipeline.run(); we just
    yield progress markers in-between.
    """
    from uuid import uuid4
    from app.audit.service import audit_service
    from app.models.design import Design, DesignDiary
    from app.services.ast_validator import ast_validator
    from app.services.triple_lock import triple_lock
    from app.services.llm_schemas import QASynthesis
    import hashlib

    run_id = uuid4().hex[:12]
    logger.info("stream_pipeline_start", run_id=run_id, user_id=str(user_id))

    try:
        # ── 1. Load materials ──────────────────────────────────────
        yield _progress("loading", "Loading material database…", 2)
        materials_by_slug = await _load_materials_by_slug(db, user_id)
        if not materials_by_slug:
            yield _sse("error", {"code": "no_materials", "message": "No materials available."})
            return

        # ── 2. Parse prompt via Claude ─────────────────────────────
        yield _progress("parsing_prompt", "Understanding your design requirements…", 8)
        try:
            request, parse_meta = await pipeline.llm.parse_bracket_prompt(
                prompt=prompt,
                available_material_slugs=sorted(materials_by_slug.keys()),
                run_id=run_id,
            )
        except Exception as exc:
            yield _sse("error", {"code": "llm_error", "message": str(exc)})
            return

        material = materials_by_slug[request.material_slug]

        # ── 3. Derive variants ─────────────────────────────────────
        yield _progress("deriving_variants", "Calculating three design alternatives…", 15)
        specs = _derive_three_variants(request)
        design_id = uuid4()

        # ── 4. Per-variant: code → AST → sandbox → analytics → Triple-Lock ──
        variant_label_names = [
            ("A", "Lightest",       35),
            ("B", "Strongest",      55),
            ("C", "Most Economical", 75),
        ]
        variant_outcomes: list[VariantOutcome] = []

        for spec, (label, friendly_name, pct) in zip(specs, variant_label_names):
            yield _progress(
                f"variant_{label.lower()}_start",
                f"Building variant {label} ({friendly_name})…",
                pct - 8,
            )

            # Emit CadQuery code
            code = _emit_cadquery_code(spec)

            # AST validate
            ast_result = ast_validator.validate(code)
            if not ast_result.valid:
                sb = SandboxResult(
                    ok=False, stage="ast",
                    error=f"template regression: {ast_result.reason}",
                )
            else:
                # Sandbox
                yield _progress(
                    f"variant_{label.lower()}_sandbox",
                    f"Running CadQuery for variant {label}…",
                    pct - 4,
                )
                sb = pipeline.sandbox.run(
                    code,
                    skip_ast_check=True,
                    run_id=f"{run_id}-{label}",
                )

                # Output validation (Layer 3)
                if sb.ok:
                    ov = pipeline.output_validator.validate(
                        sandbox_result=sb,
                        expected_base_width_mm=spec.base_width_mm,
                        expected_base_depth_mm=spec.base_depth_mm,
                        expected_wall_height_mm=spec.wall_height_mm,
                        expected_wall_thickness_mm=spec.wall_thickness_mm,
                    )
                    if not ov.passed:
                        sb = SandboxResult(
                            ok=False, stage="output_validation",
                            error=ov.summary(),
                        )

            volume_mm3 = sb.metrics.get("volume_mm3") if sb.ok else None

            # Analytics
            stress_mpa, sf, mass_kg, cost_usd = _compute_analytics(
                spec=spec,
                load_n=request.load.magnitude_n,
                lever_arm_mm=request.load.lever_arm_mm or 0.0,
                material=material,
                volume_mm3=volume_mm3,
            )

            # Triple-Lock
            tl = triple_lock.verify(lock1_value=stress_mpa)

            # DFM
            dfm_issues: list[str] = []
            try:
                from app.engines.dfm import DFMEngine
                if request.process == "cnc":
                    dfm_result = DFMEngine().check_cnc(
                        wall_thicknesses_mm=[spec.wall_thickness_mm, spec.base_thickness_mm],
                        fillet_radii_mm=[spec.fillet_radius_mm],
                        hole_diameters_mm=[spec.hole_diameter_mm],
                        hole_depths_mm=[spec.base_thickness_mm],
                        pocket_depths_mm=[],
                        material_category=material.category or "steel",
                        wall_heights_mm=[spec.wall_height_mm],
                    )
                    dfm_issues = [
                        getattr(issue, "message", str(issue))
                        for issue in getattr(dfm_result, "issues", []) or []
                    ]
            except Exception as exc:
                logger.warning("dfm_check_failed_stream", variant=label, error=str(exc))

            # Upload
            step_url: str | None = None
            glb_url: str | None = None
            if sb.ok and sb.step_path:
                try:
                    loc = await pipeline.storage.upload_design_files(
                        design_id=design_id,
                        step_path=sb.step_path,
                        glb_path=sb.glb_path,
                    )
                    step_url = loc.step.url
                    glb_url = loc.glb.url if loc.glb else None
                except Exception as exc:
                    logger.error("storage_upload_failed_stream", variant=label, error=str(exc))

            outcome = VariantOutcome(
                spec=spec,
                cadquery_code=code,
                sandbox=sb,
                analytical_stress_mpa=stress_mpa,
                mass_kg=mass_kg,
                cost_usd=cost_usd,
                safety_factor=sf,
                dfm_issues=dfm_issues,
                triple_lock=tl,
                step_url=step_url,
                glb_url=glb_url,
            )
            variant_outcomes.append(outcome)

            yield _progress(
                f"variant_{label.lower()}_done",
                f"Variant {label}: SF={sf:.1f}, mass={mass_kg:.3f} kg {'✓' if sb.ok else '✗'}",
                pct,
            )

        # ── 5. QA synthesis ────────────────────────────────────────
        yield _progress("qa_synthesis", "Writing engineering summary…", 82)
        qa = None
        qa_meta = None
        if any(o.sandbox.ok for o in variant_outcomes):
            variants_ctx = [
                {
                    "label": o.spec.label,
                    "name": o.spec.name,
                    "safety_factor": o.safety_factor,
                    "mass_kg": o.mass_kg,
                    "cost_usd": o.cost_usd,
                    "max_stress_mpa": o.analytical_stress_mpa,
                    "dfm_issues": o.dfm_issues,
                    "sandbox_ok": o.sandbox.ok,
                }
                for o in variant_outcomes
            ]
            try:
                qa, qa_meta = await pipeline.llm.synthesize_qa(
                    problem_summary=prompt[:500],
                    variants_context=variants_ctx,
                    run_id=run_id,
                )
            except Exception as exc:
                logger.warning("qa_synthesis_failed_stream", run_id=run_id, error=str(exc))

        # ── 6. Persist ─────────────────────────────────────────────
        yield _progress("saving", "Saving design…", 92)

        recommended = (
            qa.recommended_variant if qa
            else _pick_best_variant(variant_outcomes)
        )
        chosen = next(
            (o for o in variant_outcomes if o.spec.label == recommended),
            variant_outcomes[0],
        )

        design = Design(
            id=design_id,
            owner_id=user_id,
            name=_design_title(prompt),
            part_type="bracket",
            prompt=prompt,
            status=("analyzed" if chosen.sandbox.ok else "failed"),
            cadquery_code=chosen.cadquery_code,
            parameters={
                "recommended": recommended,
                "request": {
                    "material_slug": request.material_slug,
                    "process": request.process,
                    "load": {
                        "magnitude_n": request.load.magnitude_n,
                        "direction": request.load.direction,
                        "lever_arm_mm": request.load.lever_arm_mm,
                        "type": request.load.type,
                    },
                    "safety_factor_target": request.safety_factor_target,
                },
                "variants": [_variant_outcome_to_dict(o) for o in variant_outcomes],
                "qa": qa.model_dump(mode="json") if qa else None,
                "run_id": run_id,
            },
            step_url=chosen.step_url,
            glb_url=chosen.glb_url,
            lock1_results={
                "value_mpa": chosen.analytical_stress_mpa,
                "status": chosen.triple_lock.lock1.status.value if chosen.triple_lock else "error",
            } if chosen.triple_lock else None,
            lock2_results={
                "status": chosen.triple_lock.lock2.status.value,
                "note": chosen.triple_lock.lock2.note,
            } if chosen.triple_lock else None,
            lock3_results={
                "status": chosen.triple_lock.lock3.status.value,
                "note": chosen.triple_lock.lock3.note,
            } if chosen.triple_lock else None,
            confidence_score=chosen.triple_lock.confidence_score if chosen.triple_lock else None,
            confidence_explanation=chosen.triple_lock.explanation if chosen.triple_lock else None,
            material_id=material.id,
            simulation={
                "max_stress_mpa": chosen.analytical_stress_mpa,
                "safety_factor": chosen.safety_factor,
                "method": "analytical_bending_stress_shigleys_eq3_24",
            },
            dfm={"issues": chosen.dfm_issues},
            cost={"total_usd": chosen.cost_usd, "method": "v1_material_x3_markup"},
            assumptions=qa.assumptions if qa else [],
        )
        db.add(design)

        for o in variant_outcomes:
            db.add(DesignDiary(
                id=uuid4(),
                design_id=design_id,
                user_id=user_id,
                entry_type="variant_generated",
                note=(
                    f"Variant {o.spec.label} ({o.spec.name}): "
                    f"stress={o.analytical_stress_mpa:.1f} MPa, SF={o.safety_factor:.2f}"
                )[:1000],
                snapshot={
                    "variant": o.spec.label,
                    "stress_mpa": o.analytical_stress_mpa,
                    "safety_factor": o.safety_factor,
                    "mass_kg": o.mass_kg,
                    "sandbox_ok": o.sandbox.ok,
                },
            ))

        db.add(DesignDiary(
            id=uuid4(),
            design_id=design_id,
            user_id=user_id,
            entry_type="generation_complete",
            note=f"Generation complete via SSE. Recommended: {recommended}.",
            snapshot={"recommended": recommended, "run_id": run_id},
        ))

        await audit_service.log(
            session=db,
            actor_user_id=user_id,
            action="design.create",
            resource_type="design",
            resource_id=design_id,
            metadata={
                "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest()[:16],
                "material_slug": request.material_slug,
                "recommended_variant": recommended,
                "run_id": run_id,
                "via": "sse",
            },
        )

        await db.commit()

        # Re-read the persisted design
        row = (await db.execute(
            select(Design).where(Design.id == design_id)
        )).scalar_one()

        # ── 7. Emit the final `complete` event ─────────────────────
        yield _progress("done", "Design ready.", 100)
        yield _sse("complete", DesignDetail.from_row(row).model_dump())

    except Exception as exc:
        logger.exception("stream_pipeline_unhandled_error", error=str(exc))
        yield _sse("error", {"code": "internal_error", "message": str(exc)})


# ═════════════════════════════════════════════════════════════════════
# Route
# ═════════════════════════════════════════════════════════════════════

@router.post(
    "/stream",
    summary="Generate a design with SSE progress stream",
    response_class=StreamingResponse,
    responses={
        200: {"description": "SSE stream of progress events, terminated by `complete` or `error`"},
        401: {"description": "Missing or invalid bearer token"},
        422: {"description": "Prompt too short/long"},
        429: {"description": "Monthly design quota exceeded"},
    },
)
async def stream_create_design(
    payload: StreamDesignRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.DESIGN_CREATE)),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(redis_dependency),
    pipeline: GenerationPipeline = Depends(get_pipeline),
) -> StreamingResponse:
    """
    Identical auth + rate-limiting to POST /api/v1/designs, but streams
    progress via Server-Sent Events.

    **Frontend usage:**

    ```javascript
    const resp = await fetch('/api/v1/designs/stream', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({ prompt }),
    });

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const text = decoder.decode(value);
        // Parse SSE lines
        for (const line of text.split('\\n')) {
            if (line.startsWith('data: ')) {
                const event = JSON.parse(line.slice(6));
                // handle progress / complete / error
            }
        }
    }
    ```
    """
    # Rate-limit check — same logic as non-streaming POST
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

    return StreamingResponse(
        _stream_design_generation(
            prompt=payload.prompt,
            user_id=current_user.id,
            db=db,
            pipeline=pipeline,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # tell nginx/Cloudflare not to buffer
            "Connection": "keep-alive",
        },
    )

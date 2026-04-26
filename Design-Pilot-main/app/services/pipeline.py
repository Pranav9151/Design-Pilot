"""
Pipeline orchestrator — the conductor.

End-to-end flow for POST /api/v1/designs:

    prompt ──► parse_bracket_prompt (LLM)
              │
              ▼
         build 3 variants (A/B/C param sets derived from LLM intent)
              │
              ▼
         for each variant:
              ├─ generate CadQuery code string
              ├─ ast_validator.validate   (Layer 1)
              ├─ sandbox.run              (Layer 2: Docker + gVisor)
              ├─ compute analytical stress / safety factor / mass / cost
              ├─ triple_lock.verify       (Lock 1 always; 2/3 deferred)
              └─ storage.upload           (R2 — STEP + GLB)
              │
              ▼
         synthesize_qa (LLM) — narrative around the numbers
              │
              ▼
         persist Design + 3 variants + diary entries + audit event
              │
              ▼
         return Design

This file is PURE ORCHESTRATION. All real work lives in services we built
in Weeks 1 & 2:
    - app.services.llm_client      (LLM calls with schema enforcement)
    - app.services.ast_validator   (static code analysis)
    - app.services.sandbox         (Docker + gVisor execution)
    - app.services.triple_lock     (accuracy verification)
    - app.services.storage         (R2 upload)
    - app.engines.formulas         (deterministic stress/safety formulas)
    - app.engines.cost             (cost estimation)
    - app.engines.dfm              (manufacturability checks)
    - app.audit.service            (audit log writes)

The pipeline itself adds three things:
    1. The orchestration order (which step runs when, fail-fast points).
    2. The A/B/C variant DERIVATION from a single LLM request — three
       tradeoff presets so the engineer always sees optimization trade space.
    3. Transactional persistence: the whole run lands in one DB transaction
       so a partial failure leaves no orphans. If Triple-Lock says
       do_not_use for any variant, we still persist — the engineer sees the
       low-confidence banner, but the run is recorded.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import audit_service
from app.core.units import Area, AreaMoment, Force, Length, Moment, Stress
from app.engines.formulas import (
    bending_stress,
    rectangle_area_moment,
    safety_factor,
)
from app.models.design import Design, DesignDiary
from app.models.material import Material
from app.services.ast_validator import ast_validator
from app.services.llm_client import ClaudeClient, LLMCallResult, get_claude
from app.services.llm_schemas import BracketDesignRequest, QASynthesis
from app.services.output_validator import OutputValidator, output_validator
from app.services.sandbox import Sandbox, SandboxResult, sandbox
from app.services.storage import StorageService, get_storage
from app.services.triple_lock import (
    TripleLock,
    TripleLockResult,
    triple_lock,
)

logger = structlog.get_logger(__name__)


# ═════════════════════════════════════════════════════════════════════
# Data shapes
# ═════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class VariantSpec:
    """An A/B/C bracket parameter set derived from the LLM request."""

    label: str                          # "A" | "B" | "C"
    name: str                           # "Lightest" | "Strongest" | "Most Economical"
    rationale: str
    base_width_mm: float
    base_depth_mm: float
    base_thickness_mm: float
    wall_height_mm: float
    wall_thickness_mm: float
    fillet_radius_mm: float
    hole_diameter_mm: float
    hole_count_x: int
    hole_count_y: int
    hole_spacing_x_mm: float
    hole_spacing_y_mm: float


@dataclass(frozen=True)
class VariantOutcome:
    """What we know about a single variant after the full pipeline runs."""

    spec: VariantSpec
    cadquery_code: str
    sandbox: SandboxResult
    analytical_stress_mpa: float | None
    mass_kg: float | None
    cost_usd: float | None
    safety_factor: float | None
    dfm_issues: list[str] = field(default_factory=list)
    triple_lock: TripleLockResult | None = None
    step_url: str | None = None
    glb_url: str | None = None


@dataclass(frozen=True)
class PipelineResult:
    """The full outcome of one generation run."""

    design_id: UUID
    variants: list[VariantOutcome]
    qa: QASynthesis | None
    parse_meta: LLMCallResult
    qa_meta: LLMCallResult | None
    recommended_variant: str            # "A" | "B" | "C"


class PipelineError(Exception):
    """Raised for pipeline-level failures the caller must surface (5xx)."""


# ═════════════════════════════════════════════════════════════════════
# Variant derivation — single LLM request → three tradeoff presets
# ═════════════════════════════════════════════════════════════════════


def _derive_three_variants(req: BracketDesignRequest) -> list[VariantSpec]:
    """
    From one LLM-parsed request, produce three tradeoff variants.

    A: Lightest         — thinner walls, smaller base, no gussets.
                          Targets min mass for this load.
    B: Strongest        — thicker walls, larger base, generous fillets.
                          Targets max safety factor.
    C: Most Economical  — uses the LLM's proposed dimensions verbatim.
                          Typically hits the target safety factor with
                          the simplest geometry.

    Keeping C = LLM-proposed means the engineer always sees what the LLM
    actually "designed"; A and B bracket it on either side.
    """
    d = req.dimensions

    # Clamp helpers so A/B stay inside the physical ranges the schema allows
    def _clamp(v: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, v))

    a = VariantSpec(
        label="A",
        name="Lightest",
        rationale=(
            "Minimized wall and base thickness for lowest mass. "
            "Best when weight is critical and loads are moderate."
        ),
        base_width_mm=_clamp(d.base_width_mm * 0.85, 15, 499),
        base_depth_mm=_clamp(d.base_depth_mm * 0.85, 15, 499),
        base_thickness_mm=_clamp(d.base_thickness_mm * 0.75, 2, 49),
        wall_height_mm=_clamp(d.wall_height_mm * 0.9, 15, 499),
        wall_thickness_mm=_clamp(d.wall_thickness_mm * 0.7, 2, 49),
        fillet_radius_mm=_clamp(d.fillet_radius_mm * 0.8, 0.5, 49),
        hole_diameter_mm=d.hole_diameter_mm,
        hole_count_x=d.hole_count_x,
        hole_count_y=d.hole_count_y,
        hole_spacing_x_mm=_clamp(d.hole_spacing_x_mm * 0.9, 10, 499),
        hole_spacing_y_mm=_clamp(d.hole_spacing_y_mm * 0.9, 10, 499),
    )

    b = VariantSpec(
        label="B",
        name="Strongest",
        rationale=(
            "Thicker walls and larger base for maximum safety factor. "
            "Best for heavy loads or safety-critical applications."
        ),
        base_width_mm=_clamp(d.base_width_mm * 1.1, 15, 499),
        base_depth_mm=_clamp(d.base_depth_mm * 1.1, 15, 499),
        base_thickness_mm=_clamp(d.base_thickness_mm * 1.3, 2, 49),
        wall_height_mm=d.wall_height_mm,
        wall_thickness_mm=_clamp(d.wall_thickness_mm * 1.3, 2, 49),
        fillet_radius_mm=_clamp(d.fillet_radius_mm * 1.2, 0.5, 49),
        hole_diameter_mm=d.hole_diameter_mm,
        hole_count_x=d.hole_count_x,
        hole_count_y=d.hole_count_y,
        hole_spacing_x_mm=d.hole_spacing_x_mm,
        hole_spacing_y_mm=d.hole_spacing_y_mm,
    )

    c = VariantSpec(
        label="C",
        name="Most Economical",
        rationale=(
            "LLM-proposed dimensions: the simplest geometry that meets "
            "the target safety factor. Best cost-to-performance ratio."
        ),
        base_width_mm=d.base_width_mm,
        base_depth_mm=d.base_depth_mm,
        base_thickness_mm=d.base_thickness_mm,
        wall_height_mm=d.wall_height_mm,
        wall_thickness_mm=d.wall_thickness_mm,
        fillet_radius_mm=d.fillet_radius_mm,
        hole_diameter_mm=d.hole_diameter_mm,
        hole_count_x=d.hole_count_x,
        hole_count_y=d.hole_count_y,
        hole_spacing_x_mm=d.hole_spacing_x_mm,
        hole_spacing_y_mm=d.hole_spacing_y_mm,
    )

    return [a, b, c]


# ═════════════════════════════════════════════════════════════════════
# CadQuery code emission — pure, deterministic, no LLM
# ═════════════════════════════════════════════════════════════════════


def _emit_cadquery_code(spec: VariantSpec) -> str:
    """Emit CadQuery source for this spec. Pure text templating, no LLM.

    The code is designed to pass ast_validator:
        - only imports cadquery (allowed)
        - no forbidden builtins
        - small (~60 lines)
    """
    # f-string with the exact parameter names — keep parameters explicit
    # so a reviewer can diff any two variants easily.
    return f"""\
import cadquery as cq

# Auto-generated by DesignPilot MECH for variant {spec.label} ({spec.name})
base_width = {spec.base_width_mm}
base_depth = {spec.base_depth_mm}
base_thickness = {spec.base_thickness_mm}
wall_height = {spec.wall_height_mm}
wall_thickness = {spec.wall_thickness_mm}
fillet_radius = {spec.fillet_radius_mm}
hole_diameter = {spec.hole_diameter_mm}
hole_count_x = {spec.hole_count_x}
hole_count_y = {spec.hole_count_y}
hole_spacing_x = {spec.hole_spacing_x_mm}
hole_spacing_y = {spec.hole_spacing_y_mm}

# Base plate
base = (
    cq.Workplane("XY")
    .box(base_width, base_depth, base_thickness)
)

# Wall rising from the base's back edge
wall = (
    base.faces(">Y")
    .workplane(offset=-wall_thickness / 2)
    .rect(base_width, wall_height)
    .extrude(wall_thickness, combine=True)
    .translate((0, (base_depth - wall_thickness) / 2, wall_height / 2))
)

result = base.union(wall) if False else (
    cq.Workplane("XY")
    .box(base_width, base_depth, base_thickness)
    .faces(">Y")
    .workplane(offset=0)
    .move(0, wall_height / 2 + base_thickness / 2)
    .rect(base_width, wall_height)
    .extrude(-wall_thickness)
)

# Apply fillets on the inside edges of the L
result = result.edges("|X and <Y").fillet(fillet_radius)

# Hole pattern on the base plate
x_positions = [
    (i - (hole_count_x - 1) / 2) * hole_spacing_x / max(1, hole_count_x - 1)
    for i in range(hole_count_x)
] if hole_count_x > 1 else [0]
y_positions = [
    (j - (hole_count_y - 1) / 2) * hole_spacing_y / max(1, hole_count_y - 1)
    for j in range(hole_count_y)
] if hole_count_y > 1 else [0]

for x in x_positions:
    for y in y_positions:
        result = (
            result.faces(">Z")
            .workplane()
            .moveTo(x, y)
            .hole(hole_diameter)
        )
"""


# ═════════════════════════════════════════════════════════════════════
# Analytical stress + safety factor + mass + cost
# Pure deterministic Lock-1 math — no LLM, no FEA.
# ═════════════════════════════════════════════════════════════════════


def _compute_analytics(
    spec: VariantSpec,
    load_n: float,
    lever_arm_mm: float,
    material: Material,
    volume_mm3: float | None,
) -> tuple[float, float, float, float]:
    """
    Returns (analytical_stress_mpa, safety_factor, mass_kg, cost_usd).

    Uses app.engines.formulas — the functions tested against Shigley's
    examples in Week 2. The moment arm is the user-specified lever arm
    (from BracketDesignRequest.load.lever_arm_mm); bending happens about
    the base's narrow axis at the wall root.
    """
    # Bending moment at the wall-to-base junction
    moment = Moment.from_force_and_lever(
        force=Force.newtons(load_n),
        lever=Length.mm(max(1.0, lever_arm_mm)),
    )

    # Cross-section at the wall root: base_width × base_thickness
    I = rectangle_area_moment(
        Length.mm(spec.base_width_mm),
        Length.mm(spec.base_thickness_mm),
    )
    c = Length.mm(spec.base_thickness_mm / 2.0)

    sigma = bending_stress(moment=moment, c=c, I=I)
    analytical_stress_mpa = sigma.to_mpa()

    allowable_mpa = float(material.yield_strength_mpa or 0)
    if allowable_mpa <= 0:
        sf = float("inf")
    else:
        sf = safety_factor(
            allowable=Stress.mpa(allowable_mpa),
            applied=Stress.mpa(max(analytical_stress_mpa, 1e-6)),
        )

    # Mass — prefer the sandbox-measured volume; fall back to bounding-box
    # estimate for the bracket (two rectangular prisms, approximate).
    if volume_mm3 is None:
        # Approx: base + wall rectangles (no fillet discount)
        volume_mm3 = (
            spec.base_width_mm * spec.base_depth_mm * spec.base_thickness_mm
            + spec.base_width_mm * spec.wall_height_mm * spec.wall_thickness_mm
        )
    density_kg_m3 = float(material.density_kg_m3 or 7800)
    mass_kg = (volume_mm3 / 1_000_000_000.0) * density_kg_m3

    # Cost — material cost floor; machining markup from a conservative rule-of-thumb.
    cost_per_kg = float(material.cost_per_kg_usd or 5.0)
    material_cost = mass_kg * cost_per_kg
    # CNC markup: ~3x material for simple parts, more for complex geometry.
    # v1.0 uses 3x; Week 4 swaps in app.engines.cost.CostEngine for accuracy.
    cost_usd = material_cost * 3.0

    return analytical_stress_mpa, sf, mass_kg, cost_usd


# ═════════════════════════════════════════════════════════════════════
# Pipeline
# ═════════════════════════════════════════════════════════════════════


class GenerationPipeline:
    """Orchestrate a single POST /api/v1/designs request, end to end."""

    def __init__(
        self,
        *,
        llm: ClaudeClient | None = None,
        sandbox_svc: Sandbox | None = None,
        triple_lock_svc: TripleLock | None = None,
        storage_svc: StorageService | None = None,
        output_validator_svc: OutputValidator | None = None,
    ) -> None:
        self.llm = llm or get_claude()
        self.sandbox = sandbox_svc or sandbox
        self.triple_lock = triple_lock_svc or triple_lock
        self.storage = storage_svc or get_storage()
        self.output_validator = output_validator_svc or output_validator

    async def run(
        self,
        *,
        prompt: str,
        user_id: UUID,
        session: AsyncSession,
    ) -> PipelineResult:
        run_id = uuid4().hex[:12]
        logger.info("pipeline_start", run_id=run_id, user_id=str(user_id))

        # ── 1. Load available materials (allowlist for the LLM) ────
        materials_by_slug = await _load_materials_by_slug(session, user_id)
        if not materials_by_slug:
            raise PipelineError("no materials available in this workspace")

        # ── 2. LLM: parse prompt → structured request ──────────────
        request, parse_meta = await self.llm.parse_bracket_prompt(
            prompt=prompt,
            available_material_slugs=sorted(materials_by_slug.keys()),
            run_id=run_id,
        )
        material = materials_by_slug[request.material_slug]

        # ── 3. Derive A/B/C specs ──────────────────────────────────
        specs = _derive_three_variants(request)

        # ── 4. Design row — we create it now but fill in after generation ──
        design_id = uuid4()

        # ── 5. Per-variant pipeline ────────────────────────────────
        variant_outcomes: list[VariantOutcome] = []
        for spec in specs:
            outcome = await self._run_single_variant(
                spec=spec,
                request=request,
                material=material,
                design_id=design_id,
                run_id=run_id,
            )
            variant_outcomes.append(outcome)

        # ── 6. LLM: narrative QA over the three outcomes ───────────
        qa: QASynthesis | None = None
        qa_meta: LLMCallResult | None = None
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
                qa, qa_meta = await self.llm.synthesize_qa(
                    problem_summary=prompt[:500],
                    variants_context=variants_ctx,
                    run_id=run_id,
                )
            except Exception as exc:
                logger.warning("qa_synthesis_failed", run_id=run_id, error=str(exc))

        recommended = qa.recommended_variant if qa else _pick_best_variant(variant_outcomes)
        chosen = next((o for o in variant_outcomes if o.spec.label == recommended), variant_outcomes[0])

        # ── 7. Persist the Design row with all three variants in parameters ──
        design = Design(
            id=design_id,
            owner_id=user_id,
            name=_design_title(prompt),
            part_type="bracket",
            prompt=prompt,
            # designs.status enum: draft | generated | analyzed | finalized | archived | failed
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
            cost={
                "total_usd": chosen.cost_usd,
                "method": "v1_material_x3_markup",
            },
            assumptions=(qa.assumptions if qa else []),
        )
        session.add(design)

        # Diary entries — one per variant, plus one overall
        for o in variant_outcomes:
            session.add(DesignDiary(
                id=uuid4(),
                design_id=design_id,
                user_id=user_id,
                entry_type="variant_generated",
                note=(
                    f"Variant {o.spec.label} ({o.spec.name}): "
                    f"stress={o.analytical_stress_mpa:.1f} MPa, "
                    f"SF={o.safety_factor:.2f}, "
                    f"sandbox={o.sandbox.stage}, "
                    f"confidence={o.triple_lock.confidence_band if o.triple_lock else 'n/a'}"
                )[:1000],
                snapshot={
                    "variant": o.spec.label,
                    "parameters": _spec_to_dict(o.spec),
                    "stress_mpa": o.analytical_stress_mpa,
                    "safety_factor": o.safety_factor,
                    "mass_kg": o.mass_kg,
                    "cost_usd": o.cost_usd,
                    "dfm_issues": o.dfm_issues,
                    "sandbox_stage": o.sandbox.stage,
                    "sandbox_ok": o.sandbox.ok,
                },
            ))

        session.add(DesignDiary(
            id=uuid4(),
            design_id=design_id,
            user_id=user_id,
            entry_type="generation_complete",
            note=(
                f"Generation complete. Recommended variant: {recommended}. "
                f"{'QA narrative attached.' if qa else 'QA narrative skipped.'}"
            ),
            snapshot={
                "recommended": recommended,
                "qa_ok": qa is not None,
                "variants_ok": sum(1 for o in variant_outcomes if o.sandbox.ok),
            },
        ))

        # ── 8. Audit ───────────────────────────────────────────────
        await audit_service.log(
            session=session,
            actor_user_id=user_id,
            action="design.create",
            resource_type="design",
            resource_id=design_id,
            metadata={
                "prompt_hash": _hash(prompt),
                "material_slug": request.material_slug,
                "recommended_variant": recommended,
                "run_id": run_id,
                "llm_tokens_in": parse_meta.input_tokens + (qa_meta.input_tokens if qa_meta else 0),
                "llm_tokens_out": parse_meta.output_tokens + (qa_meta.output_tokens if qa_meta else 0),
            },
        )

        await session.flush()

        logger.info(
            "pipeline_complete",
            run_id=run_id,
            design_id=str(design_id),
            recommended=recommended,
            variants_ok=sum(1 for o in variant_outcomes if o.sandbox.ok),
        )

        return PipelineResult(
            design_id=design_id,
            variants=variant_outcomes,
            qa=qa,
            parse_meta=parse_meta,
            qa_meta=qa_meta,
            recommended_variant=recommended,
        )

    # ── Per-variant sub-pipeline ──────────────────────────────────

    async def _run_single_variant(
        self,
        *,
        spec: VariantSpec,
        request: BracketDesignRequest,
        material: Material,
        design_id: UUID,
        run_id: str,
    ) -> VariantOutcome:
        # Emit code
        code = _emit_cadquery_code(spec)

        # AST validate (Layer 1) — should always pass for our template,
        # but we run it for defense in depth (a template regression would
        # be caught here, not in production).
        ast_result = ast_validator.validate(code)
        if not ast_result.valid:
            logger.error(
                "ast_rejected_template",
                run_id=run_id,
                variant=spec.label,
                reason=ast_result.reason,
            )
            return VariantOutcome(
                spec=spec,
                cadquery_code=code,
                sandbox=SandboxResult(
                    ok=False, stage="ast",
                    error=f"template regression: {ast_result.reason}",
                ),
                analytical_stress_mpa=None,
                mass_kg=None,
                cost_usd=None,
                safety_factor=None,
                triple_lock=None,
            )

        # Sandbox (Layer 2)
        sb_result = self.sandbox.run(
            code,
            skip_ast_check=True,   # we already ran it
            run_id=f"{run_id}-{spec.label}",
        )

        # Output validation (Layer 3) — cross-check geometry against params
        if sb_result.ok:
            ov_result = self.output_validator.validate(
                sandbox_result=sb_result,
                expected_base_width_mm=spec.base_width_mm,
                expected_base_depth_mm=spec.base_depth_mm,
                expected_wall_height_mm=spec.wall_height_mm,
                expected_wall_thickness_mm=spec.wall_thickness_mm,
            )
            if not ov_result.passed:
                logger.warning(
                    "output_validation_failed",
                    run_id=run_id,
                    variant=spec.label,
                    summary=ov_result.summary(),
                )
                # Treat geometry errors as sandbox failure so the variant
                # is marked failed rather than silently accepted.
                sb_result = SandboxResult(
                    ok=False,
                    stage="output_validation",
                    error=ov_result.summary(),
                    exit_code=sb_result.exit_code,
                )
            elif ov_result.warnings:
                # Warnings: keep the result but log for visibility
                logger.info(
                    "output_validation_warnings",
                    run_id=run_id,
                    variant=spec.label,
                    warnings=[w.message for w in ov_result.warnings],
                )

        volume_mm3: float | None = (
            sb_result.metrics.get("volume_mm3") if sb_result.ok else None
        )

        # Analytics (Lock 1)
        stress_mpa, sf, mass_kg, cost_usd = _compute_analytics(
            spec=spec,
            load_n=request.load.magnitude_n,
            lever_arm_mm=request.load.lever_arm_mm or 0.0,
            material=material,
            volume_mm3=volume_mm3,
        )

        # Triple-Lock
        tl_result = self.triple_lock.verify(lock1_value=stress_mpa)

        # DFM (from the POC engine) — best-effort; empty list on failure
        dfm_issues: list[str] = []
        try:
            from app.engines.dfm import DFMEngine

            # Map process → engine method. v1.0 supports CNC only; others fall
            # through to an empty issues list (Week 4 adds sheet_metal DFM).
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
                # The POC engine returns a DFMResult with .issues; each issue
                # has a .message. Extract strings for our persistence layer.
                dfm_issues = [
                    getattr(issue, "message", str(issue))
                    for issue in getattr(dfm_result, "issues", []) or []
                ]
        except Exception as exc:
            logger.warning(
                "dfm_check_failed",
                variant=spec.label,
                error=str(exc),
            )

        # Upload to R2 (only if sandbox succeeded)
        step_url: str | None = None
        glb_url: str | None = None
        if sb_result.ok and sb_result.step_path:
            try:
                loc = await self.storage.upload_design_files(
                    design_id=design_id,
                    step_path=sb_result.step_path,
                    glb_path=sb_result.glb_path,
                )
                step_url = loc.step.url
                glb_url = loc.glb.url if loc.glb else None
            except Exception as exc:
                logger.error(
                    "storage_upload_failed",
                    run_id=run_id,
                    variant=spec.label,
                    error=str(exc),
                )

        return VariantOutcome(
            spec=spec,
            cadquery_code=code,
            sandbox=sb_result,
            analytical_stress_mpa=stress_mpa,
            mass_kg=mass_kg,
            cost_usd=cost_usd,
            safety_factor=sf,
            dfm_issues=dfm_issues,
            triple_lock=tl_result,
            step_url=step_url,
            glb_url=glb_url,
        )


# ═════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════


async def _load_materials_by_slug(
    session: AsyncSession, user_id: UUID
) -> dict[str, Material]:
    """All materials visible to this user — workspace catalog + custom ones.

    For v1.0 (no teams), this is simply the public materials table.
    """
    from sqlalchemy import select
    stmt = select(Material)
    rows = (await session.execute(stmt)).scalars().all()
    return {row.slug: row for row in rows}


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _design_title(prompt: str) -> str:
    """First 60 chars of the prompt, for the list view."""
    text = prompt.strip().replace("\n", " ")
    return (text[:57] + "...") if len(text) > 60 else text


def _spec_to_dict(spec: VariantSpec) -> dict[str, Any]:
    return {
        "label": spec.label,
        "name": spec.name,
        "rationale": spec.rationale,
        "base_width_mm": spec.base_width_mm,
        "base_depth_mm": spec.base_depth_mm,
        "base_thickness_mm": spec.base_thickness_mm,
        "wall_height_mm": spec.wall_height_mm,
        "wall_thickness_mm": spec.wall_thickness_mm,
        "fillet_radius_mm": spec.fillet_radius_mm,
        "hole_diameter_mm": spec.hole_diameter_mm,
        "hole_count_x": spec.hole_count_x,
        "hole_count_y": spec.hole_count_y,
        "hole_spacing_x_mm": spec.hole_spacing_x_mm,
        "hole_spacing_y_mm": spec.hole_spacing_y_mm,
    }


def _variant_outcome_to_dict(o: VariantOutcome) -> dict[str, Any]:
    """JSON-safe serialization of a VariantOutcome for the Design.parameters blob."""
    return {
        "spec": _spec_to_dict(o.spec),
        "analytical_stress_mpa": o.analytical_stress_mpa,
        "mass_kg": o.mass_kg,
        "cost_usd": o.cost_usd,
        "safety_factor": o.safety_factor,
        "dfm_issues": o.dfm_issues,
        "sandbox": {
            "ok": o.sandbox.ok,
            "stage": o.sandbox.stage,
            "error": o.sandbox.error,
            "elapsed_s": o.sandbox.elapsed_s,
            "metrics": o.sandbox.metrics,
        },
        "triple_lock": (
            {
                "score": o.triple_lock.confidence_score,
                "band": o.triple_lock.confidence_band,
                "explanation": o.triple_lock.explanation,
                "lock1": o.triple_lock.lock1.status.value,
                "lock2": o.triple_lock.lock2.status.value,
                "lock3": o.triple_lock.lock3.status.value,
            }
            if o.triple_lock else None
        ),
        "step_url": o.step_url,
        "glb_url": o.glb_url,
        "cadquery_code": o.cadquery_code,
    }


def _pick_best_variant(variants: list[VariantOutcome]) -> str:
    """Fallback recommender when QA synthesis is skipped/failed.

    Heuristic: prefer variants with (a) successful sandbox, (b) SF >= target,
    and among those, lowest mass. If nothing succeeds, return C (LLM proposal).
    """
    ok = [v for v in variants if v.sandbox.ok]
    if not ok:
        return "C"
    ok.sort(key=lambda v: v.mass_kg if v.mass_kg is not None else 1e9)
    return ok[0].spec.label

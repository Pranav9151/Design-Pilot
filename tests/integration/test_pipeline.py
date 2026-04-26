"""
End-to-end pipeline integration test.

We stub the two external dependencies (Anthropic API, Docker sandbox)
with deterministic fakes. Everything else is real:
  - Postgres, RLS, migrations
  - SQLAlchemy ORM models
  - The Triple-Lock orchestrator
  - Engineering formulas
  - Audit log writes (including the append-only DB trigger)
  - DesignDiary writes
  - Storage (local-fallback mode)

If this test passes, the entire Week 3 stack is wired correctly
end-to-end. It's the most important single test in the codebase right now.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.design import Design, DesignDiary
from app.models.material import Material
from app.services.llm_client import ClaudeClient, LLMCallResult
from app.services.llm_schemas import BracketDesignRequest, QASynthesis
from app.services.pipeline import GenerationPipeline, PipelineResult
from app.services.sandbox import Sandbox, SandboxResult


# ═════════════════════════════════════════════════════════════════════
# Fakes
# ═════════════════════════════════════════════════════════════════════


class FakeClaudeClient:
    """Returns pre-baked BracketDesignRequest and QASynthesis — no API calls."""

    def __init__(self, material_slug: str = "aluminum_6061_t6"):
        self._material_slug = material_slug

    async def parse_bracket_prompt(
        self, prompt: str, *, available_material_slugs: list[str], run_id: str | None = None,
    ):
        assert self._material_slug in available_material_slugs
        req = BracketDesignRequest.model_validate({
            "part_type": "bracket",
            "material_slug": self._material_slug,
            "process": "cnc",
            "load": {
                "type": "static_point",
                "magnitude_n": 490.5,
                "direction": "down",
                "lever_arm_mm": 100.0,
            },
            "dimensions": {
                "base_width_mm": 80.0,
                "base_depth_mm": 60.0,
                "base_thickness_mm": 8.0,
                "wall_height_mm": 50.0,
                "wall_thickness_mm": 6.0,
                "fillet_radius_mm": 5.0,
                "hole_diameter_mm": 9.0,
                "hole_count_x": 2,
                "hole_count_y": 2,
                "hole_spacing_x_mm": 50.0,
                "hole_spacing_y_mm": 30.0,
            },
            "safety_factor_target": 2.5,
            "rationale": (
                "Chose 6061-T6 for good machinability and corrosion resistance. "
                "Dimensions are a balanced starting point for the 50 kg static load."
            ),
        })
        meta = LLMCallResult(
            model="claude-sonnet-4-fake",
            input_tokens=100, output_tokens=400,
            cache_read_tokens=0, cache_creation_tokens=0,
            latency_ms=250, retries=0,
        )
        return req, meta

    async def synthesize_qa(self, *, problem_summary: str, variants_context: list[dict], run_id: str | None = None):
        qa = QASynthesis.model_validate({
            "recommended_variant": "C",
            "summary": (
                "Three variants generated. Variant C (the balanced LLM proposal) "
                "is recommended for this static 50 kg load case."
            ),
            "why_recommended": (
                "Variant C meets the safety factor target with the simplest geometry. "
                "Straightforward to CNC in one setup."
            ),
            "why_not_a": "Variant A is thinner and loses margin on cyclic loading.",
            "why_not_b": "Variant B is heavier than necessary for this load case.",
            "why_not_c": "Variant C is the recommendation.",
            "senior_engineer_questions": [
                "Is the load truly static, or should we account for cyclic?",
                "Does the mounting surface have significant tolerance slop?",
            ],
            "assumptions": [
                "Static load at the centre of the lever arm",
                "Ambient operating temperature",
            ],
        })
        meta = LLMCallResult(
            model="claude-sonnet-4-fake",
            input_tokens=200, output_tokens=500,
            cache_read_tokens=0, cache_creation_tokens=0,
            latency_ms=300, retries=0,
        )
        return qa, meta


class FakeSandbox:
    """Simulates a successful sandbox run — creates a fake STEP file on disk."""

    def __init__(self, tmp_root: Path, volume_mm3: float = 60_000.0):
        self._tmp_root = tmp_root
        self._volume_mm3 = volume_mm3

    def run(self, code: str, *, skip_ast_check: bool = False, use_gvisor: bool | None = None, run_id: str | None = None):
        out_dir = self._tmp_root / f"sandbox-out-{run_id or uuid4().hex[:8]}"
        out_dir.mkdir(parents=True, exist_ok=True)
        step_path = out_dir / "part.step"
        step_path.write_bytes(
            b"ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION((''),'2;1');\nENDSEC;\n"
            b"DATA;\n#1=FAKE_PART(" + (code[:200].encode("utf-8")) + b");\n"
            b"ENDSEC;\nEND-ISO-10303-21;\n"
        )
        return SandboxResult(
            ok=True,
            stage="success",
            step_path=step_path,
            glb_path=None,
            metrics={
                "volume_mm3": self._volume_mm3,
                "bbox_x_size": 80.0, "bbox_y_size": 60.0, "bbox_z_size": 58.0,
            },
            warnings=[],
            elapsed_s=0.01,
            exit_code=0,
        )


class FakeFailingSandbox:
    """Sandbox that fails at the 'execute' stage (simulates bad CadQuery output)."""

    def run(self, code: str, **kwargs):
        return SandboxResult(
            ok=False,
            stage="execute",
            error="simulated CadQuery runtime error",
        )


# ═════════════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def seeded_user(db) -> UUID:
    """Create a user in the DB so FK constraints on designs / diary pass."""
    from sqlalchemy import text
    uid = uuid4()
    await db.execute(text(
        "INSERT INTO users (id, email, name, plan) "
        "VALUES (:id, :email, :name, :plan)"
    ), {"id": uid, "email": f"{uid}@test.local", "name": "Pipeline Test", "plan": "free"})
    return uid


@pytest.fixture
def pipeline_with_fakes(tmp_path, monkeypatch):
    """A GenerationPipeline with fake LLM + sandbox, real DB + storage (local)."""
    from app.services.storage import StorageService
    storage = StorageService()
    # Force local-fallback mode
    monkeypatch.setattr(storage.settings, "R2_ACCOUNT_ID", "")
    monkeypatch.setattr(storage.settings, "R2_ACCESS_KEY_ID", "")
    monkeypatch.setattr(storage.settings, "R2_SECRET_ACCESS_KEY", "")
    monkeypatch.setattr(storage.settings, "R2_ENDPOINT_URL", "")
    storage._local_fallback_dir = tmp_path / "r2-local"

    return GenerationPipeline(
        llm=FakeClaudeClient(),
        sandbox_svc=FakeSandbox(tmp_path),
        storage_svc=storage,
    )


# ═════════════════════════════════════════════════════════════════════
# Happy path
# ═════════════════════════════════════════════════════════════════════


async def test_pipeline_runs_end_to_end_and_persists_design(db, seed_materials_in_test_db, seeded_user, pipeline_with_fakes
):
    """Full happy path: LLM parses, 3 variants all pass sandbox, QA synthesizes,
    Design row persists with recommended variant C's data promoted to canonical fields."""
    result = await pipeline_with_fakes.run(
        prompt="Aluminum L-bracket for 50 kg static load on a 100 mm lever arm.",
        user_id=seeded_user,
        session=db,
    )

    assert isinstance(result, PipelineResult)
    assert len(result.variants) == 3
    assert {v.spec.label for v in result.variants} == {"A", "B", "C"}
    assert result.recommended_variant == "C"    # from the fake QA
    assert result.qa is not None
    assert result.qa.recommended_variant == "C"

    # Design row landed in DB
    await db.flush()
    db_design = (await db.execute(
        select(Design).where(Design.id == result.design_id)
    )).scalar_one()
    assert db_design is not None
    assert db_design.owner_id == seeded_user
    assert db_design.status == "analyzed"
    assert db_design.step_url is not None      # recommended variant's STEP url
    assert db_design.confidence_score is not None
    # All three variants captured in the parameters JSONB
    assert "variants" in db_design.parameters
    assert len(db_design.parameters["variants"]) == 3


async def test_pipeline_writes_diary_entries_per_variant(db, seed_materials_in_test_db, seeded_user, pipeline_with_fakes
):
    result = await pipeline_with_fakes.run(
        prompt="L-bracket for 50 kg",
        user_id=seeded_user,
        session=db,
    )
    await db.flush()

    diary = (await db.execute(
        select(DesignDiary).where(DesignDiary.design_id == result.design_id)
        .order_by(DesignDiary.created_at)
    )).scalars().all()

    # 3 per-variant + 1 overall = 4 entries minimum
    assert len(diary) >= 4
    types = {d.entry_type for d in diary}
    assert "variant_generated" in types
    assert "generation_complete" in types


async def test_pipeline_writes_audit_event(db, seed_materials_in_test_db, seeded_user, pipeline_with_fakes
):
    result = await pipeline_with_fakes.run(
        prompt="L-bracket for 50 kg",
        user_id=seeded_user,
        session=db,
    )
    await db.flush()

    audit = (await db.execute(
        select(AuditLog).where(AuditLog.action == "design.create")
    )).scalars().all()
    assert len(audit) == 1
    assert str(audit[0].resource_id) == str(result.design_id)
    assert audit[0].actor_user_id == seeded_user
    # Metadata captured
    assert audit[0].metadata_["recommended_variant"] == "C"


async def test_pipeline_analytical_stress_is_in_expected_range(db, seed_materials_in_test_db, seeded_user, pipeline_with_fakes
):
    """
    For our fake request (490.5 N × 100 mm lever arm, variant C base 80×8 mm):
      σ = M·c/I = 49,050 × 4 / (80 × 8³/12)
        = 49,050 × 4 / 3413.33
        = 57.5 MPa

    Let's sanity-check the pipeline's computed value is in the right neighbourhood.
    """
    result = await pipeline_with_fakes.run(
        prompt="L-bracket for 50 kg",
        user_id=seeded_user,
        session=db,
    )
    variant_c = next(v for v in result.variants if v.spec.label == "C")
    assert variant_c.analytical_stress_mpa is not None
    # Within 10% of the hand calculation
    assert 50.0 <= variant_c.analytical_stress_mpa <= 65.0

    # Safety factor should be healthy — 6061-T6 yield = 276 MPa, applied ~57 MPa → SF ~4.8
    assert variant_c.safety_factor is not None
    assert 3.5 <= variant_c.safety_factor <= 6.0


async def test_pipeline_triple_lock_is_good_not_high_with_empty_rag(db, seed_materials_in_test_db, seeded_user, pipeline_with_fakes
):
    """Honesty check: with no knowledge base populated yet, confidence is
    'good' (single active lock), never 'high'."""
    result = await pipeline_with_fakes.run(
        prompt="L-bracket for 50 kg",
        user_id=seeded_user,
        session=db,
    )
    for v in result.variants:
        assert v.triple_lock is not None
        assert v.triple_lock.confidence_band in ("good", "review"), (
            f"v1.0 should never hit 'high' band with empty RAG; "
            f"variant {v.spec.label} was {v.triple_lock.confidence_band}"
        )


async def test_pipeline_uploads_step_to_storage(db, seed_materials_in_test_db, seeded_user, pipeline_with_fakes, tmp_path
):
    result = await pipeline_with_fakes.run(
        prompt="L-bracket for 50 kg",
        user_id=seeded_user,
        session=db,
    )

    # Every variant that succeeded should have a step_url
    for v in result.variants:
        if v.sandbox.ok:
            assert v.step_url is not None
            assert v.step_url.startswith("file://")


# ═════════════════════════════════════════════════════════════════════
# Failure modes
# ═════════════════════════════════════════════════════════════════════


async def test_pipeline_marks_design_failed_when_all_variants_fail(db, seed_materials_in_test_db, seeded_user, tmp_path, monkeypatch
):
    from app.services.storage import StorageService
    storage = StorageService()
    monkeypatch.setattr(storage.settings, "R2_ACCESS_KEY_ID", "")
    monkeypatch.setattr(storage.settings, "R2_ENDPOINT_URL", "")
    storage._local_fallback_dir = tmp_path / "r2-local"

    pipeline = GenerationPipeline(
        llm=FakeClaudeClient(),
        sandbox_svc=FakeFailingSandbox(),
        storage_svc=storage,
    )
    result = await pipeline.run(
        prompt="L-bracket that will fail",
        user_id=seeded_user,
        session=db,
    )

    # No variant succeeded → no QA was attempted
    assert result.qa is None
    # But the design row still persisted (with status=failed) for postmortem
    await db.flush()
    db_design = (await db.execute(
        select(Design).where(Design.id == result.design_id)
    )).scalar_one()
    assert db_design.status == "failed"


async def test_pipeline_errors_when_no_materials_available(db, seed_materials_in_test_db, seeded_user, pipeline_with_fakes
):
    """If the materials table is empty, we must fail fast — no LLM call."""
    from sqlalchemy import text
    from app.services.pipeline import PipelineError

    await db.execute(text("DELETE FROM materials"))
    await db.flush()

    with pytest.raises(PipelineError) as exc_info:
        await pipeline_with_fakes.run(
            prompt="L-bracket",
            user_id=seeded_user,
            session=db,
        )
    assert "materials" in str(exc_info.value).lower()

"""
Integration tests for the Week 5 AI-powered design endpoints.

Tests the six endpoints that were added after the base CRUD:
  PATCH  /api/v1/designs/{id}/parameters   — re-run analytics with new dims
  POST   /api/v1/designs/{id}/explain      — manager-friendly summary
  GET    /api/v1/designs/{id}/why-not      — recommendation reasoning
  GET    /api/v1/designs/{id}/similar      — semantic design search
  GET    /api/v1/designs/{id}/questions    — senior engineer review questions
  POST   /api/v1/designs/{id}/optimize     — goal-directed optimization

Every test:
 - Uses real Postgres + Alembic schema (through the integration fixture chain)
 - Stubs LLM and sandbox with deterministic fakes (no Anthropic API calls)
 - Uses fakeredis so rate-limiting logic exercises without real Redis
 - Verifies status codes, response shapes, DB side-effects, and audit events
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
import fakeredis.aioredis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.models.audit import AuditLog
from app.models.design import Design, DesignDiary
from app.services.llm_client import LLMCallResult
from app.services.llm_schemas import BracketDesignRequest, QASynthesis
from app.services.pipeline import GenerationPipeline
from app.services.sandbox import SandboxResult

pytestmark = [pytest.mark.integration]


# ═════════════════════════════════════════════════════════════════════
# Shared fakes (mirrors test_pipeline.py without circular imports)
# ═════════════════════════════════════════════════════════════════════


class FakeLLM:
    """Deterministic LLM fake — no network calls."""

    SLUG = "aluminum_6061_t6"

    async def parse_bracket_prompt(
        self, prompt: str, *, available_material_slugs: list[str], run_id: str | None = None
    ):
        req = BracketDesignRequest.model_validate({
            "part_type": "bracket",
            "material_slug": self.SLUG,
            "process": "cnc",
            "load": {"type": "static_point", "magnitude_n": 490.5, "direction": "down", "lever_arm_mm": 100.0},
            "dimensions": {
                "base_width_mm": 80.0, "base_depth_mm": 60.0, "base_thickness_mm": 8.0,
                "wall_height_mm": 50.0, "wall_thickness_mm": 6.0, "fillet_radius_mm": 5.0,
                "hole_diameter_mm": 9.0, "hole_count_x": 2, "hole_count_y": 2,
                "hole_spacing_x_mm": 50.0, "hole_spacing_y_mm": 30.0,
            },
            "safety_factor_target": 2.5,
            "rationale": "Chose 6061-T6 for machinability and corrosion resistance on this 50 kg static load.",
        })
        meta = LLMCallResult(
            model="fake", input_tokens=100, output_tokens=400,
            cache_read_tokens=0, cache_creation_tokens=0, latency_ms=10, retries=0,
        )
        return req, meta

    async def synthesize_qa(self, *, problem_summary: str, variants_context: list, run_id: str | None = None):
        qa = QASynthesis.model_validate({
            "recommended_variant": "C",
            "summary": "Three variants generated for a 50 kg static bracket.",
            "why_recommended": "Variant C meets the safety factor with the simplest geometry.",
            "why_not_a": "Variant A is thinner and loses margin on cyclic loading.",
            "why_not_b": "Variant B is heavier than necessary for this static load.",
            "why_not_c": "Variant C is the recommendation.",
            "senior_engineer_questions": [
                "Is the load truly static or could it see dynamic shock?",
                "Does the mounting surface have significant tolerance slop at the bolt holes?",
                "Have you verified wall thickness clears the tool change envelope for CNC?",
            ],
            "assumptions": [
                "Static point load at the end of the lever arm.",
                "Ambient temperature operating conditions.",
            ],
        })
        meta = LLMCallResult(
            model="fake", input_tokens=200, output_tokens=500,
            cache_read_tokens=0, cache_creation_tokens=0, latency_ms=10, retries=0,
        )
        return qa, meta


class FakeSandbox:
    """Returns ok=True with a minimal STEP file."""

    def __init__(self, tmp_root: Path):
        self._root = tmp_root

    def run(self, code: str, *, skip_ast_check: bool = False,
            use_gvisor: bool | None = None, run_id: str | None = None) -> SandboxResult:
        out = self._root / f"sb-{run_id or uuid4().hex[:8]}"
        out.mkdir(parents=True, exist_ok=True)
        step = out / "part.step"
        step.write_bytes(
            b"ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION((''),'2;1');\nENDSEC;\n"
            b"DATA;\nENDSEC;\nEND-ISO-10303-21;\n"
        )
        return SandboxResult(
            ok=True, stage="success",
            step_path=step, glb_path=None,
            metrics={"volume_mm3": 60_000.0, "bbox_x_size": 80.0, "bbox_y_size": 60.0, "bbox_z_size": 58.0},
            warnings=[], elapsed_s=0.01, exit_code=0,
        )


# ═════════════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def user_id(db) -> UUID:
    uid = uuid4()
    await db.execute(text(
        "INSERT INTO users (id, email, name, plan) VALUES (:id, :email, :name, :plan)"
    ), {"id": uid, "email": f"{uid}@ai-endpoint-test.local", "name": "AI Test", "plan": "pro"})
    return uid


@pytest.fixture
def fake_pipeline(tmp_path, monkeypatch):
    from app.services.storage import StorageService
    storage = StorageService()
    monkeypatch.setattr(storage.settings, "R2_ACCOUNT_ID", "")
    monkeypatch.setattr(storage.settings, "R2_ACCESS_KEY_ID", "")
    monkeypatch.setattr(storage.settings, "R2_SECRET_ACCESS_KEY", "")
    monkeypatch.setattr(storage.settings, "R2_ENDPOINT_URL", "")
    storage._local_fallback_dir = tmp_path / "r2"
    return GenerationPipeline(llm=FakeLLM(), sandbox_svc=FakeSandbox(tmp_path), storage_svc=storage)


@pytest.fixture
def http_client(fake_pipeline):
    """Async HTTP client wired to the FastAPI app with faked deps."""
    from app.main import app
    from app.api.v1.designs import get_pipeline
    from app.core.redis_client import redis_dependency

    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=False)

    async def _pipeline():
        return fake_pipeline

    async def _redis():
        yield fake_redis

    app.dependency_overrides[get_pipeline] = _pipeline
    app.dependency_overrides[redis_dependency] = _redis
    yield app
    app.dependency_overrides.pop(get_pipeline, None)
    app.dependency_overrides.pop(redis_dependency, None)


@pytest_asyncio.fixture
async def seeded_design(http_client, user_id, seed_materials_in_test_db, jwt_alice, db) -> dict:
    """Create a real design via POST /api/v1/designs and return the JSON body.

    Uses jwt_alice (stable UUID 1111…) instead of user_id so the JWT sub
    matches the DB row. We insert alice's user row here.
    """
    from tests.conftest import make_jwt

    alice_id = UUID("11111111-1111-1111-1111-111111111111")
    await db.execute(text(
        "INSERT INTO users (id, email, name, plan) VALUES (:id, :email, :name, :plan) "
        "ON CONFLICT (id) DO NOTHING"
    ), {"id": alice_id, "email": "alice@example.com", "name": "Alice", "plan": "pro"})
    await db.commit()

    transport = ASGITransport(app=http_client)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/v1/designs",
            json={"prompt": "6061-T6 aluminium L-bracket, 50 kg static load, 100 mm arm, CNC, M8 bolts."},
            headers={"Authorization": f"Bearer {jwt_alice}"},
        )
    assert r.status_code == 201, f"seed design failed: {r.text}"
    return r.json()


# ═════════════════════════════════════════════════════════════════════
# Helper
# ═════════════════════════════════════════════════════════════════════


def _auth(jwt: str) -> dict:
    return {"Authorization": f"Bearer {jwt}"}


# ═════════════════════════════════════════════════════════════════════
# POST /api/v1/designs  (baseline — confirms fixture works)
# ═════════════════════════════════════════════════════════════════════


async def test_create_design_baseline(http_client, seed_materials_in_test_db, jwt_alice, db):
    """Sanity check: POST returns 201 with required fields."""
    alice_id = UUID("11111111-1111-1111-1111-111111111111")
    await db.execute(text(
        "INSERT INTO users (id, email, name, plan) VALUES (:id, :email, :name, :plan) "
        "ON CONFLICT (id) DO NOTHING"
    ), {"id": alice_id, "email": "alice@example.com", "name": "Alice", "plan": "pro"})
    await db.commit()

    transport = ASGITransport(app=http_client)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/v1/designs",
            json={"prompt": "Aluminium bracket for 50 kg static load on 100 mm arm CNC M8 bolts."},
            headers=_auth(jwt_alice),
        )
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "analyzed"
    assert body["part_type"] == "bracket"
    assert len(body["parameters"]["variants"]) == 3
    assert body["parameters"]["recommended"] in ("A", "B", "C")
    assert body["confidence_score"] is not None
    assert body["step_url"] is not None


# ═════════════════════════════════════════════════════════════════════
# GET /api/v1/designs/{id}/why-not
# ═════════════════════════════════════════════════════════════════════


async def test_why_not_returns_reasoning(http_client, seeded_design, jwt_alice):
    design_id = seeded_design["id"]
    transport = ASGITransport(app=http_client)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(f"/api/v1/designs/{design_id}/why-not", headers=_auth(jwt_alice))
    assert r.status_code == 200
    body = r.json()
    assert body["recommended_variant"] in ("A", "B", "C")
    assert body["why_recommended"]
    assert body["why_not_a"]
    assert body["why_not_b"]
    assert body["why_not_c"]


async def test_why_not_404_for_missing(http_client, jwt_alice, seed_materials_in_test_db, db):
    alice_id = UUID("11111111-1111-1111-1111-111111111111")
    await db.execute(text(
        "INSERT INTO users (id, email, name, plan) VALUES (:id, :email, :name, :plan) "
        "ON CONFLICT (id) DO NOTHING"
    ), {"id": alice_id, "email": "alice@example.com", "name": "Alice", "plan": "pro"})
    await db.commit()

    transport = ASGITransport(app=http_client)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(f"/api/v1/designs/{uuid4()}/why-not", headers=_auth(jwt_alice))
    assert r.status_code == 404


async def test_why_not_cross_user_returns_404(http_client, seeded_design, jwt_bob, db):
    """Bob must never see Alice's design reasoning — 404 not 403."""
    bob_id = UUID("22222222-2222-2222-2222-222222222222")
    await db.execute(text(
        "INSERT INTO users (id, email, name, plan) VALUES (:id, :email, :name, :plan) "
        "ON CONFLICT (id) DO NOTHING"
    ), {"id": bob_id, "email": "bob@example.com", "name": "Bob", "plan": "pro"})
    await db.commit()

    design_id = seeded_design["id"]
    transport = ASGITransport(app=http_client)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(f"/api/v1/designs/{design_id}/why-not", headers=_auth(jwt_bob))
    assert r.status_code == 404, "Must not leak design existence to other users"


# ═════════════════════════════════════════════════════════════════════
# GET /api/v1/designs/{id}/questions
# ═════════════════════════════════════════════════════════════════════


async def test_questions_returns_list(http_client, seeded_design, jwt_alice):
    design_id = seeded_design["id"]
    transport = ASGITransport(app=http_client)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(f"/api/v1/designs/{design_id}/questions", headers=_auth(jwt_alice))
    assert r.status_code == 200
    body = r.json()
    assert "questions" in body
    assert isinstance(body["questions"], list)
    assert 1 <= len(body["questions"]) <= 5
    assert all(isinstance(q, str) and len(q) > 10 for q in body["questions"])


async def test_questions_requires_auth(http_client, seeded_design):
    design_id = seeded_design["id"]
    transport = ASGITransport(app=http_client)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(f"/api/v1/designs/{design_id}/questions")
    assert r.status_code == 401


# ═════════════════════════════════════════════════════════════════════
# GET /api/v1/designs/{id}/similar
# ═════════════════════════════════════════════════════════════════════


async def test_similar_returns_list(http_client, seeded_design, jwt_alice):
    design_id = seeded_design["id"]
    transport = ASGITransport(app=http_client)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(
            f"/api/v1/designs/{design_id}/similar?limit=4",
            headers=_auth(jwt_alice),
        )
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert isinstance(body["items"], list)
    # With only one design, similar list must be empty (you can't be similar to yourself)
    for item in body["items"]:
        assert item["id"] != design_id, "A design must not appear in its own similar list"


async def test_similar_scores_between_0_and_1(http_client, seeded_design, jwt_alice):
    """Every similarity score must be in [0, 1]."""
    design_id = seeded_design["id"]
    transport = ASGITransport(app=http_client)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(f"/api/v1/designs/{design_id}/similar", headers=_auth(jwt_alice))
    assert r.status_code == 200
    for item in r.json()["items"]:
        assert 0.0 <= item["similarity_score"] <= 1.0


# ═════════════════════════════════════════════════════════════════════
# POST /api/v1/designs/{id}/explain
# ═════════════════════════════════════════════════════════════════════


async def test_explain_returns_summary(http_client, seeded_design, jwt_alice):
    design_id = seeded_design["id"]
    transport = ASGITransport(app=http_client)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(f"/api/v1/designs/{design_id}/explain", headers=_auth(jwt_alice))
    assert r.status_code == 200
    body = r.json()
    assert "summary" in body
    assert len(body["summary"]) > 50, "Summary too short to be useful"


async def test_explain_mentions_variant(http_client, seeded_design, jwt_alice):
    """The summary must mention the recommended variant label."""
    design_id = seeded_design["id"]
    recommended = seeded_design["parameters"]["recommended"]
    transport = ASGITransport(app=http_client)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(f"/api/v1/designs/{design_id}/explain", headers=_auth(jwt_alice))
    assert r.status_code == 200
    summary = r.json()["summary"]
    assert f"Variant {recommended}" in summary


async def test_explain_cross_user_returns_404(http_client, seeded_design, jwt_bob, db):
    bob_id = UUID("22222222-2222-2222-2222-222222222222")
    await db.execute(text(
        "INSERT INTO users (id, email, name, plan) VALUES (:id, :email, :name, :plan) "
        "ON CONFLICT (id) DO NOTHING"
    ), {"id": bob_id, "email": "bob@example.com", "name": "Bob", "plan": "pro"})
    await db.commit()

    design_id = seeded_design["id"]
    transport = ASGITransport(app=http_client)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(f"/api/v1/designs/{design_id}/explain", headers=_auth(jwt_bob))
    assert r.status_code == 404


# ═════════════════════════════════════════════════════════════════════
# PATCH /api/v1/designs/{id}/parameters
# ═════════════════════════════════════════════════════════════════════


async def test_patch_parameters_returns_updated_design(http_client, seeded_design, jwt_alice):
    design_id = seeded_design["id"]
    original_thickness = seeded_design["parameters"]["variants"][0]["spec"]["base_thickness_mm"]

    new_thickness = original_thickness + 2.0
    transport = ASGITransport(app=http_client)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.patch(
            f"/api/v1/designs/{design_id}/parameters",
            json={"base_thickness_mm": new_thickness},
            headers=_auth(jwt_alice),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == design_id
    # Status should be analyzed (re-run) or at least not failed
    assert body["status"] in ("analyzed", "generated", "draft")


async def test_patch_parameters_increments_version(http_client, seeded_design, jwt_alice, db):
    design_id = seeded_design["id"]
    transport = ASGITransport(app=http_client)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.patch(
            f"/api/v1/designs/{design_id}/parameters",
            json={"wall_thickness_mm": 8.0},
            headers=_auth(jwt_alice),
        )
    # Re-fetch from DB
    from uuid import UUID as _UUID
    row = (await db.execute(
        select(Design).where(Design.id == _UUID(design_id))
    )).scalar_one()
    assert row.version >= 1, "Version must be incremented after parameter patch"


async def test_patch_parameters_writes_diary_entry(http_client, seeded_design, jwt_alice, db):
    design_id = seeded_design["id"]
    transport = ASGITransport(app=http_client)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.patch(
            f"/api/v1/designs/{design_id}/parameters",
            json={"wall_height_mm": 60.0},
            headers=_auth(jwt_alice),
        )
    from uuid import UUID as _UUID
    entries = (await db.execute(
        select(DesignDiary).where(
            DesignDiary.design_id == _UUID(design_id),
            DesignDiary.entry_type == "parameter_change",
        )
    )).scalars().all()
    assert len(entries) >= 1


async def test_patch_parameters_validates_bounds(http_client, seeded_design, jwt_alice):
    """Out-of-range values must be rejected with 422."""
    design_id = seeded_design["id"]
    transport = ASGITransport(app=http_client)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.patch(
            f"/api/v1/designs/{design_id}/parameters",
            # base_thickness_mm max is 50 per Pydantic schema
            json={"base_thickness_mm": 999.0},
            headers=_auth(jwt_alice),
        )
    assert r.status_code == 422


async def test_patch_parameters_cross_user_returns_404(http_client, seeded_design, jwt_bob, db):
    bob_id = UUID("22222222-2222-2222-2222-222222222222")
    await db.execute(text(
        "INSERT INTO users (id, email, name, plan) VALUES (:id, :email, :name, :plan) "
        "ON CONFLICT (id) DO NOTHING"
    ), {"id": bob_id, "email": "bob@example.com", "name": "Bob", "plan": "pro"})
    await db.commit()

    design_id = seeded_design["id"]
    transport = ASGITransport(app=http_client)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.patch(
            f"/api/v1/designs/{design_id}/parameters",
            json={"wall_thickness_mm": 8.0},
            headers=_auth(jwt_bob),
        )
    assert r.status_code == 404


# ═════════════════════════════════════════════════════════════════════
# POST /api/v1/designs/{id}/optimize
# ═════════════════════════════════════════════════════════════════════


async def test_optimize_returns_design_and_goal(http_client, seeded_design, jwt_alice):
    design_id = seeded_design["id"]
    transport = ASGITransport(app=http_client)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            f"/api/v1/designs/{design_id}/optimize",
            json={"goal": "Reduce mass while keeping safety factor above 2.0."},
            headers=_auth(jwt_alice),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "goal" in body
    assert "recommended_variant" in body
    assert body["recommended_variant"] in ("A", "B", "C")
    assert "design" in body
    assert body["design"]["id"] == design_id


async def test_optimize_writes_diary_entry(http_client, seeded_design, jwt_alice, db):
    design_id = seeded_design["id"]
    transport = ASGITransport(app=http_client)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post(
            f"/api/v1/designs/{design_id}/optimize",
            json={"goal": "Make this bracket lighter without reducing strength margin."},
            headers=_auth(jwt_alice),
        )
    from uuid import UUID as _UUID
    entries = (await db.execute(
        select(DesignDiary).where(
            DesignDiary.design_id == _UUID(design_id),
            DesignDiary.entry_type == "optimize",
        )
    )).scalars().all()
    assert len(entries) >= 1


async def test_optimize_increments_version(http_client, seeded_design, jwt_alice, db):
    design_id = seeded_design["id"]
    transport = ASGITransport(app=http_client)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post(
            f"/api/v1/designs/{design_id}/optimize",
            json={"goal": "Optimise for minimum cost while keeping the safety factor above 1.5."},
            headers=_auth(jwt_alice),
        )
    from uuid import UUID as _UUID
    row = (await db.execute(
        select(Design).where(Design.id == _UUID(design_id))
    )).scalar_one()
    assert row.version >= 1


async def test_optimize_rejects_short_goal(http_client, seeded_design, jwt_alice):
    design_id = seeded_design["id"]
    transport = ASGITransport(app=http_client)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            f"/api/v1/designs/{design_id}/optimize",
            json={"goal": "lighter"},  # < 10 chars — Pydantic min_length
            headers=_auth(jwt_alice),
        )
    assert r.status_code == 422


async def test_optimize_cross_user_returns_404(http_client, seeded_design, jwt_bob, db):
    bob_id = UUID("22222222-2222-2222-2222-222222222222")
    await db.execute(text(
        "INSERT INTO users (id, email, name, plan) VALUES (:id, :email, :name, :plan) "
        "ON CONFLICT (id) DO NOTHING"
    ), {"id": bob_id, "email": "bob@example.com", "name": "Bob", "plan": "pro"})
    await db.commit()

    design_id = seeded_design["id"]
    transport = ASGITransport(app=http_client)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            f"/api/v1/designs/{design_id}/optimize",
            json={"goal": "Reduce mass while maintaining safety above 2.0."},
            headers=_auth(jwt_bob),
        )
    assert r.status_code == 404


# ═════════════════════════════════════════════════════════════════════
# Audit log — cross-endpoint check
# ═════════════════════════════════════════════════════════════════════


async def test_parameter_patch_writes_audit_log(http_client, seeded_design, jwt_alice, db):
    design_id = seeded_design["id"]
    transport = ASGITransport(app=http_client)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.patch(
            f"/api/v1/designs/{design_id}/parameters",
            json={"fillet_radius_mm": 6.0},
            headers=_auth(jwt_alice),
        )
    from uuid import UUID as _UUID
    audit_rows = (await db.execute(
        select(AuditLog).where(AuditLog.action == "design.parameters.patch")
    )).scalars().all()
    assert len(audit_rows) >= 1
    assert str(audit_rows[0].resource_id) == design_id


async def test_optimize_writes_audit_log(http_client, seeded_design, jwt_alice, db):
    design_id = seeded_design["id"]
    transport = ASGITransport(app=http_client)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post(
            f"/api/v1/designs/{design_id}/optimize",
            json={"goal": "Minimise bracket mass for a lightweight aerospace application."},
            headers=_auth(jwt_alice),
        )
    from uuid import UUID as _UUID
    audit_rows = (await db.execute(
        select(AuditLog).where(AuditLog.action == "design.optimize")
    )).scalars().all()
    assert len(audit_rows) >= 1

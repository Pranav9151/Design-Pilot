"""
Integration: /api/v1/designs.

POST is the crown-jewel endpoint. We stub the LLM + sandbox (as in
test_pipeline.py) but run everything else — auth, permissions, rate
limiting, RLS, DB writes — for real.

Test matrix:
    - POST without auth             → 401
    - POST bad prompt (too short)   → 422
    - POST happy path               → 201 + DesignDetail with 3 variants
    - POST at cap triggers          → 429 with Retry-After headers
    - GET list returns user's rows  → 200, RLS-filtered
    - GET by id for own design      → 200
    - GET by id for other user's    → 404 (never leak membership)
    - DELETE moves to archived      → 204
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from tests.integration.test_pipeline import FakeClaudeClient, FakeSandbox


pytestmark = [pytest.mark.integration]


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def seeded_alice(db, user_id_alice: UUID) -> UUID:
    """Insert alice into the users table so FK constraints pass.

    We COMMIT because the API endpoint runs in its own session; an
    uncommitted insert in the test fixture's session would be invisible
    to the endpoint's db session.
    """
    await db.execute(text(
        "INSERT INTO users (id, email, name, plan) "
        "VALUES (:id, :email, :name, :plan) "
        "ON CONFLICT (id) DO NOTHING"
    ), {"id": user_id_alice, "email": "alice@example.com", "name": "Alice", "plan": "free"})
    await db.commit()
    return user_id_alice


@pytest_asyncio.fixture
async def seeded_bob(db, user_id_bob: UUID) -> UUID:
    await db.execute(text(
        "INSERT INTO users (id, email, name, plan) "
        "VALUES (:id, :email, :name, :plan) "
        "ON CONFLICT (id) DO NOTHING"
    ), {"id": user_id_bob, "email": "bob@example.com", "name": "Bob", "plan": "free"})
    await db.commit()
    return user_id_bob


@pytest.fixture
def client_with_fakes(tmp_path: Path, monkeypatch):
    """An httpx client wired to the FastAPI app, with the pipeline + redis deps
    overridden to use fakes (no real LLM, no Docker, no real Redis)."""
    from app.main import app
    from app.api.v1.designs import get_pipeline
    from app.core.redis_client import redis_dependency
    from app.services.pipeline import GenerationPipeline
    from app.services.storage import StorageService
    import fakeredis.aioredis

    # Storage: local fallback
    storage = StorageService()
    monkeypatch.setattr(storage.settings, "R2_ACCOUNT_ID", "")
    monkeypatch.setattr(storage.settings, "R2_ACCESS_KEY_ID", "")
    monkeypatch.setattr(storage.settings, "R2_SECRET_ACCESS_KEY", "")
    monkeypatch.setattr(storage.settings, "R2_ENDPOINT_URL", "")
    storage._local_fallback_dir = tmp_path / "r2-local"

    fake_pipeline = GenerationPipeline(
        llm=FakeClaudeClient(),
        sandbox_svc=FakeSandbox(tmp_path),
        storage_svc=storage,
    )

    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=False)

    async def override_pipeline():
        return fake_pipeline

    async def override_redis():
        yield fake_redis

    app.dependency_overrides[get_pipeline] = override_pipeline
    app.dependency_overrides[redis_dependency] = override_redis

    transport = ASGITransport(app=app)

    async def _build():
        return AsyncClient(transport=transport, base_url="http://test")

    yield _build, fake_redis

    # Cleanup
    app.dependency_overrides.pop(get_pipeline, None)
    app.dependency_overrides.pop(redis_dependency, None)


# ─────────────────────────────────────────────────────────────────────
# POST /api/v1/designs
# ─────────────────────────────────────────────────────────────────────


async def test_post_requires_auth(client_with_fakes):
    build, _ = client_with_fakes
    async with await build() as c:
        r = await c.post("/api/v1/designs", json={"prompt": "L-bracket for 50 kg"})
    assert r.status_code == 401


async def test_post_rejects_short_prompt(
    client_with_fakes, jwt_alice, seeded_alice, seed_materials_in_test_db
):
    build, _ = client_with_fakes
    async with await build() as c:
        r = await c.post(
            "/api/v1/designs",
            json={"prompt": "too short"},
            headers={"Authorization": f"Bearer {jwt_alice}"},
        )
    assert r.status_code == 422


async def test_post_happy_path_returns_design_detail(
    client_with_fakes, jwt_alice, seeded_alice, seed_materials_in_test_db
):
    build, _ = client_with_fakes
    async with await build() as c:
        r = await c.post(
            "/api/v1/designs",
            json={"prompt": "Aluminum L-bracket for 50 kg static load on 100 mm arm."},
            headers={"Authorization": f"Bearer {jwt_alice}"},
        )
    assert r.status_code == 201, r.text
    body = r.json()

    # Shape: DesignDetail with all the Week 3 fields
    assert body["status"] == "analyzed"
    assert body["part_type"] == "bracket"
    assert body["step_url"] is not None
    assert body["confidence_score"] is not None
    assert body["confidence_explanation"] is not None
    assert body["cadquery_code"] is not None

    # The three variants are embedded in parameters
    assert "variants" in body["parameters"]
    assert len(body["parameters"]["variants"]) == 3
    labels = {v["spec"]["label"] for v in body["parameters"]["variants"]}
    assert labels == {"A", "B", "C"}

    # Triple-Lock honest banding
    for v in body["parameters"]["variants"]:
        assert v["triple_lock"]["band"] in ("good", "review")
        assert v["triple_lock"]["band"] != "high"   # empty RAG — must NOT claim high

    # Recommended variant is surfaced
    assert body["parameters"]["recommended"] in ("A", "B", "C")


async def test_post_rate_limit_429_after_cap(
    client_with_fakes, jwt_alice, seeded_alice, seed_materials_in_test_db
):
    """Free tier is 5/month. The 6th request this month must be 429."""
    build, _ = client_with_fakes
    prompt = {"prompt": "L-bracket for 50 kg static load on 100 mm arm."}
    headers = {"Authorization": f"Bearer {jwt_alice}"}

    async with await build() as c:
        # 5 successful creates
        for i in range(5):
            r = await c.post("/api/v1/designs", json=prompt, headers=headers)
            assert r.status_code == 201, f"request {i+1} failed: {r.text}"

        # 6th is over the cap
        r = await c.post("/api/v1/designs", json=prompt, headers=headers)
    assert r.status_code == 429
    body = r.json()
    assert body["detail"]["error"] == "rate_limited"
    assert body["detail"]["limit"] == 5
    assert body["detail"]["current"] >= 6
    # Headers are advisory — present for clients that honour them
    assert r.headers.get("x-ratelimit-limit") == "5"
    assert r.headers.get("x-ratelimit-remaining") == "0"


# ─────────────────────────────────────────────────────────────────────
# GET /api/v1/designs  (list)
# ─────────────────────────────────────────────────────────────────────


async def test_get_list_returns_own_designs(
    client_with_fakes, jwt_alice, seeded_alice, seed_materials_in_test_db
):
    build, _ = client_with_fakes
    prompt = {"prompt": "L-bracket for 50 kg static load on 100 mm arm."}
    headers = {"Authorization": f"Bearer {jwt_alice}"}

    async with await build() as c:
        # Create two designs
        await c.post("/api/v1/designs", json=prompt, headers=headers)
        await c.post("/api/v1/designs", json=prompt, headers=headers)

        # List them
        r = await c.get("/api/v1/designs", headers=headers)

    assert r.status_code == 200
    items = r.json()
    assert len(items) == 2
    for item in items:
        assert item["status"] == "analyzed"
        assert item["step_url"] is not None


# ─────────────────────────────────────────────────────────────────────
# GET /api/v1/designs/:id  +  RLS cross-user defense
# ─────────────────────────────────────────────────────────────────────


async def test_get_detail_for_own_design(
    client_with_fakes, jwt_alice, seeded_alice, seed_materials_in_test_db
):
    build, _ = client_with_fakes
    headers = {"Authorization": f"Bearer {jwt_alice}"}

    async with await build() as c:
        r_post = await c.post(
            "/api/v1/designs",
            json={"prompt": "L-bracket for 50 kg static load on 100 mm arm."},
            headers=headers,
        )
        assert r_post.status_code == 201
        design_id = r_post.json()["id"]

        r_get = await c.get(f"/api/v1/designs/{design_id}", headers=headers)

    assert r_get.status_code == 200
    assert r_get.json()["id"] == design_id


async def test_get_detail_returns_404_for_nonexistent(
    client_with_fakes, jwt_alice, seeded_alice
):
    build, _ = client_with_fakes
    async with await build() as c:
        r = await c.get(
            f"/api/v1/designs/{uuid4()}",
            headers={"Authorization": f"Bearer {jwt_alice}"},
        )
    assert r.status_code == 404


async def test_bob_cannot_see_alices_design(
    client_with_fakes,
    jwt_alice, seeded_alice,
    jwt_bob, seeded_bob,
    seed_materials_in_test_db,
):
    """RLS defense: alice creates a design; bob must get 404 (NOT 403)."""
    build, _ = client_with_fakes
    async with await build() as c:
        r_alice = await c.post(
            "/api/v1/designs",
            json={"prompt": "Alice's L-bracket for 50 kg static load."},
            headers={"Authorization": f"Bearer {jwt_alice}"},
        )
        assert r_alice.status_code == 201
        alice_design_id = r_alice.json()["id"]

        r_bob = await c.get(
            f"/api/v1/designs/{alice_design_id}",
            headers={"Authorization": f"Bearer {jwt_bob}"},
        )
    assert r_bob.status_code == 404, (
        "Leaking existence of other users' designs via 403 is a side-channel. "
        "Must return 404."
    )


# ─────────────────────────────────────────────────────────────────────
# DELETE /api/v1/designs/:id  (soft-delete)
# ─────────────────────────────────────────────────────────────────────


async def test_delete_marks_design_archived(
    client_with_fakes, jwt_alice, seeded_alice, seed_materials_in_test_db
):
    build, _ = client_with_fakes
    headers = {"Authorization": f"Bearer {jwt_alice}"}

    async with await build() as c:
        r_post = await c.post(
            "/api/v1/designs",
            json={"prompt": "L-bracket for 50 kg static load on 100 mm arm."},
            headers=headers,
        )
        design_id = r_post.json()["id"]

        r_del = await c.delete(f"/api/v1/designs/{design_id}", headers=headers)
        assert r_del.status_code == 204

        # GET confirms archived
        r_get = await c.get(f"/api/v1/designs/{design_id}", headers=headers)
    assert r_get.status_code == 200
    assert r_get.json()["status"] == "archived"


async def test_stream_route_is_not_captured_by_design_id_path(
    client_with_fakes, jwt_alice, seeded_alice, seed_materials_in_test_db
):
    build, _ = client_with_fakes
    async with await build() as c:
        r = await c.post(
            "/api/v1/designs/stream",
            json={"prompt": "L-bracket for 50 kg static load on 100 mm arm."},
            headers={"Authorization": f"Bearer {jwt_alice}"},
        )
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    assert "event: complete" in r.text


async def test_patch_parameters_rebuilds_design(
    client_with_fakes, jwt_alice, seeded_alice, seed_materials_in_test_db
):
    build, _ = client_with_fakes
    headers = {"Authorization": f"Bearer {jwt_alice}"}
    async with await build() as c:
        created = await c.post(
            "/api/v1/designs",
            json={"prompt": "L-bracket for 50 kg static load on 100 mm arm."},
            headers=headers,
        )
        design_id = created.json()["id"]
        patched = await c.patch(
            f"/api/v1/designs/{design_id}/parameters",
            json={"wall_thickness_mm": 12, "recommended_variant": "B"},
            headers=headers,
        )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["parameters"]["recommended"] == "B"
    assert body["parameters"]["variants"][0]["spec"]["wall_thickness_mm"] == 12


async def test_explain_questions_similar_and_optimize_endpoints(
    client_with_fakes, jwt_alice, seeded_alice, seed_materials_in_test_db
):
    build, _ = client_with_fakes
    headers = {"Authorization": f"Bearer {jwt_alice}"}
    async with await build() as c:
        first = await c.post(
            "/api/v1/designs",
            json={"prompt": "Aluminum L-bracket for 50 kg static load on 100 mm arm."},
            headers=headers,
        )
        second = await c.post(
            "/api/v1/designs",
            json={"prompt": "Aluminum L-bracket for 60 kg static load on 100 mm arm."},
            headers=headers,
        )
        design_id = first.json()["id"]

        explain = await c.post(f"/api/v1/designs/{design_id}/explain", headers=headers)
        questions = await c.get(f"/api/v1/designs/{design_id}/questions", headers=headers)
        similar = await c.get(f"/api/v1/designs/{design_id}/similar", headers=headers)
        optimize = await c.post(
            f"/api/v1/designs/{design_id}/optimize",
            json={"goal": "Minimize weight while keeping a healthy safety margin."},
            headers=headers,
        )

    assert second.status_code == 201
    assert explain.status_code == 200
    assert "Variant" in explain.json()["summary"]
    assert questions.status_code == 200
    assert len(questions.json()["questions"]) >= 1
    assert similar.status_code == 200
    assert len(similar.json()["items"]) >= 1
    assert optimize.status_code == 200, optimize.text
    assert optimize.json()["design"]["parameters"]["recommended"] in ("A", "B", "C")

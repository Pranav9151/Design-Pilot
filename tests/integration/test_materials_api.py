"""
Integration: /api/v1/materials.

Contract:
- unauth → 401
- authenticated + MATERIAL_VIEW permission (all users have it) → 200 + items
- category filter honoured
- min_yield_mpa filter honoured
- invalid category → 400
- /{slug} returns 404 for unknown slug
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


pytestmark = [pytest.mark.integration]


@pytest.fixture
async def client():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_materials_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/materials")
    assert r.status_code == 401


async def test_materials_invalid_token_rejected(client: AsyncClient):
    r = await client.get(
        "/api/v1/materials",
        headers={"Authorization": "Bearer not-a-real-jwt"},
    )
    assert r.status_code == 401


async def test_materials_list_returns_seeded_rows(
    client: AsyncClient,
    jwt_alice: str,
    seed_materials_in_test_db,
):
    r = await client.get(
        "/api/v1/materials",
        headers={"Authorization": f"Bearer {jwt_alice}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 12
    assert len(body["items"]) == 12

    # Spot-check a well-known row
    by_slug = {m["slug"]: m for m in body["items"]}
    al6061 = by_slug["aluminum_6061_t6"]
    assert al6061["yield_strength_mpa"] == 276
    assert al6061["density_kg_m3"] == 2710
    assert "ASM Handbook" in al6061["source"]


async def test_materials_filter_by_category(
    client: AsyncClient,
    jwt_alice: str,
    seed_materials_in_test_db,
):
    r = await client.get(
        "/api/v1/materials?category=aluminum",
        headers={"Authorization": f"Bearer {jwt_alice}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 3  # 6061, 7075, 5052
    for item in body["items"]:
        assert item["category"] == "aluminum"


async def test_materials_filter_min_yield(
    client: AsyncClient,
    jwt_alice: str,
    seed_materials_in_test_db,
):
    """Only materials with yield ≥ 500 MPa. Titanium, 7075, 1045, 4140."""
    r = await client.get(
        "/api/v1/materials?min_yield_mpa=500",
        headers={"Authorization": f"Bearer {jwt_alice}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 4
    for item in body["items"]:
        assert item["yield_strength_mpa"] >= 500


async def test_materials_invalid_category_400(
    client: AsyncClient,
    jwt_alice: str,
    seed_materials_in_test_db,
):
    r = await client.get(
        "/api/v1/materials?category=unobtainium",
        headers={"Authorization": f"Bearer {jwt_alice}"},
    )
    assert r.status_code == 400
    assert "category" in r.json()["detail"].lower()


async def test_material_by_slug(
    client: AsyncClient,
    jwt_alice: str,
    seed_materials_in_test_db,
):
    r = await client.get(
        "/api/v1/materials/steel_4140",
        headers={"Authorization": f"Bearer {jwt_alice}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "steel_4140"
    assert body["grade"] == "AISI 4140 (Q&T)"
    assert body["yield_strength_mpa"] == 655


async def test_material_by_slug_404(
    client: AsyncClient,
    jwt_alice: str,
    seed_materials_in_test_db,
):
    r = await client.get(
        "/api/v1/materials/unobtainium",
        headers={"Authorization": f"Bearer {jwt_alice}"},
    )
    assert r.status_code == 404


async def test_materials_list_request_creates_audit_log_entry(
    client: AsyncClient,
    jwt_alice: str,
    seed_materials_in_test_db,
    db: AsyncSession,
):
    """The audit middleware should NOT log reads (GET), but POST/PATCH/DELETE are logged.
    Confirm GET /materials is intentionally NOT audited (too noisy)."""
    before = (await db.execute(text("SELECT count(*) FROM audit_log"))).scalar_one()

    r = await client.get(
        "/api/v1/materials",
        headers={"Authorization": f"Bearer {jwt_alice}"},
    )
    assert r.status_code == 200

    after = (await db.execute(text("SELECT count(*) FROM audit_log"))).scalar_one()
    assert after == before, "Audit middleware is logging GETs — should be POST/PATCH/DELETE only"

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


pytestmark = [pytest.mark.integration]


@pytest.fixture
async def client():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_material_recommendations(
    client: AsyncClient,
    jwt_alice: str,
    seed_materials_in_test_db,
):
    r = await client.post(
        "/api/v1/materials/recommend",
        headers={"Authorization": f"Bearer {jwt_alice}"},
        json={
            "load_n": 1200,
            "process": "cnc",
            "environment": "outdoor",
            "prioritize": "balanced",
            "top_k": 3,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["items"]) == 3
    assert all(item["reasons"] for item in body["items"])

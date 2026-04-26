"""
Integration: /health (liveness, no DB) and /ready (DB check) endpoints.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


pytestmark = [pytest.mark.integration]


@pytest.fixture
async def client():
    """Async test client against the real FastAPI app."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health_returns_200(client: AsyncClient):
    """Liveness probe: always 200, never touches DB."""
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


async def test_api_v1_health_returns_200(client: AsyncClient):
    """The /api/v1/health variant exposes more detail."""
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["app"] == "DesignPilot MECH"
    assert "version" in body
    assert "env" in body


async def test_ready_returns_200_when_db_alive(client: AsyncClient):
    """Readiness probe does a SELECT 1 and must succeed."""
    r = await client.get("/api/v1/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["db"] == "ok"


async def test_security_headers_present(client: AsyncClient):
    """Every response carries our baseline security headers."""
    r = await client.get("/health")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    assert "referrer-policy" in r.headers


async def test_request_id_header_echoed(client: AsyncClient):
    """Audit middleware attaches an X-Request-ID to every response."""
    r = await client.get("/health")
    assert "x-request-id" in r.headers
    assert len(r.headers["x-request-id"]) >= 32  # UUID4 string

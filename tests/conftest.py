"""
Shared pytest fixtures.

Strategy:
- Session-scoped fixture runs `alembic upgrade head` against the TEST DB once.
- Each test function gets a fresh transaction that is rolled back at teardown.
- For tests that need to bypass RLS (admin / fixture setup), we use
  `SET LOCAL app.current_user_id` in fixtures to impersonate a user.

The TEST DB is configured via DATABASE_URL / DATABASE_URL_SYNC in .env.test.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

# Force test env before any app imports pick up settings
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://designpilot:designpilot@localhost:5433/designpilot_test",
)
os.environ.setdefault(
    "DATABASE_URL_SYNC",
    "postgresql://designpilot:designpilot@localhost:5433/designpilot_test",
)
os.environ.setdefault(
    "SUPABASE_JWT_SECRET", "test-secret-at-least-32-characters-long-xxxxx"
)
os.environ.setdefault("SECRET_KEY", "test-secret-at-least-32-characters-long-xxxxx")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from app.core.config import get_settings  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def apply_migrations():
    """Apply all Alembic migrations against the test DB once per session."""
    import subprocess

    settings = get_settings()
    env = os.environ.copy()
    env["DATABASE_URL_SYNC"] = settings.DATABASE_URL_SYNC

    # alembic downgrade base first to guarantee a clean slate
    subprocess.run(
        ["alembic", "downgrade", "base"],
        cwd=str(PROJECT_ROOT),
        env=env,
        check=False,
        capture_output=True,
    )
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=str(PROJECT_ROOT),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    print(f"\n[conftest] alembic upgrade head:\n{result.stdout}")

    yield


@pytest_asyncio.fixture
async def engine():
    """Per-test async engine (small pool). Disposed cleanly at teardown."""
    settings = get_settings()
    eng = create_async_engine(settings.DATABASE_URL, pool_size=2, max_overflow=1)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _reset_app_engine():
    """
    Reset the app-level engine singleton between tests so each test gets a
    fresh engine bound to the current event loop. Without this, asyncpg
    connections opened in a previous test's loop fail to close at teardown
    ('Event loop is closed').
    """
    # Before test: ensure no stale engine is cached
    import app.core.db as core_db
    if core_db._engine is not None:
        try:
            await core_db._engine.dispose()
        except Exception:
            pass
        core_db._engine = None
        core_db._session_factory = None
    yield
    # After test: dispose any engine the app acquired
    if core_db._engine is not None:
        try:
            await core_db._engine.dispose()
        except Exception:
            pass
        core_db._engine = None
        core_db._session_factory = None


@pytest_asyncio.fixture
async def db(engine) -> AsyncIterator[AsyncSession]:
    """Per-test DB session. Truncates user-data tables after each test."""
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        yield session
        await session.rollback()

    # Clean user-data tables between tests so ordering is stable.
    # Ref tables are preserved:
    #   - materials: seeded by scripts/seed_materials (or the fixture)
    #   - roles (is_system=TRUE rows): seeded by the initial migration
    #
    # audit_log has an append-only trigger + privilege revocation. For test
    # teardown we SET session_replication_role TO replica to bypass triggers,
    # then truncate. This is SAFE because our test DB is ephemeral; a real
    # attacker cannot toggle session_replication_role without superuser.
    async with factory() as cleanup:
        await cleanup.execute(text("SET session_replication_role = 'replica'"))
        await cleanup.execute(text("TRUNCATE TABLE audit_log"))
        await cleanup.execute(text("SET session_replication_role = 'origin'"))
        await cleanup.execute(text("DELETE FROM design_feedback"))
        await cleanup.execute(text("DELETE FROM design_diary"))
        await cleanup.execute(text("DELETE FROM knowledge_chunks"))
        await cleanup.execute(text("DELETE FROM designs"))
        await cleanup.execute(text("DELETE FROM team_members"))
        await cleanup.execute(text("DELETE FROM custom_materials"))
        # roles: keep system roles (is_system=TRUE), drop any team-created ones
        await cleanup.execute(text("DELETE FROM roles WHERE is_system = FALSE"))
        await cleanup.execute(text("DELETE FROM teams"))
        await cleanup.execute(text("DELETE FROM users"))
        await cleanup.commit()


@pytest_asyncio.fixture
async def seed_materials_in_test_db(db: AsyncSession):
    """Ensure the materials table is seeded for tests that need it."""
    # The migration seeds system roles but NOT materials.
    # We call the seed script programmatically so tests don't depend on shell.
    from app.data.materials import MATERIALS

    result = await db.execute(text("SELECT count(*) FROM materials"))
    count = result.scalar_one()
    if count >= len(MATERIALS):
        return

    for slug, mat in MATERIALS.items():
        await db.execute(
            text("""
                INSERT INTO materials (slug, name, grade, category,
                    youngs_modulus_mpa, yield_strength_mpa, ultimate_strength_mpa,
                    density_kg_m3, poissons_ratio, elongation_percent,
                    cte, thermal_conductivity, max_service_temp_c,
                    machinability_rating, cost_per_kg_usd, source)
                VALUES (:slug, :name, :grade, :category,
                    :ym, :ys, :us, :rho, :nu, :el, :cte, :tc, :mt, :mr, :cost, :src)
                ON CONFLICT (slug) DO NOTHING
            """),
            {
                "slug": slug, "name": mat.name, "grade": mat.grade, "category": mat.category,
                "ym": mat.youngs_modulus_mpa, "ys": mat.yield_strength_mpa,
                "us": mat.ultimate_strength_mpa, "rho": mat.density_kg_m3,
                "nu": mat.poissons_ratio, "el": mat.elongation_percent,
                "cte": mat.cte, "tc": mat.thermal_conductivity,
                "mt": mat.max_service_temp_c, "mr": mat.machinability_rating,
                "cost": mat.cost_per_kg_usd, "src": mat.source,
            },
        )
    await db.commit()


@pytest.fixture
def user_id_alice() -> UUID:
    """Stable UUID for 'alice' in multi-user isolation tests."""
    return UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def user_id_bob() -> UUID:
    """Stable UUID for 'bob' in multi-user isolation tests."""
    return UUID("22222222-2222-2222-2222-222222222222")


def make_jwt(user_id: UUID, email: str = "test@example.com") -> str:
    """Create a signed JWT that the app's Supabase JWT validator will accept."""
    from datetime import datetime, timedelta, timezone

    from jose import jwt

    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "aud": settings.SUPABASE_JWT_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
        "role": "authenticated",
    }
    return jwt.encode(
        payload,
        settings.SUPABASE_JWT_SECRET,
        algorithm=settings.SUPABASE_JWT_ALGORITHM,
    )


@pytest.fixture
def jwt_alice(user_id_alice: UUID) -> str:
    return make_jwt(user_id_alice, email="alice@example.com")


@pytest.fixture
def jwt_bob(user_id_bob: UUID) -> str:
    return make_jwt(user_id_bob, email="bob@example.com")


@pytest.fixture
def jwt_random() -> str:
    return make_jwt(uuid4(), email="random@example.com")

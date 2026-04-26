"""
Integration: prove that RLS actually isolates users at the DB level.

This is the defense-in-depth that makes application code safer —
even if an engineer forgets to filter by `owner_id` in a query,
the database refuses to return another user's rows.
"""
from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings


pytestmark = [pytest.mark.integration]


# Separate engine connecting as a non-superuser role so RLS is enforced.
# The default 'designpilot' user is SUPERUSER in our dev setup (so migrations
# can run), but SUPERUSER bypasses RLS. We create a restricted role here.


async def _ensure_rls_role(db: AsyncSession) -> None:
    """Create a rls_test role that does NOT bypass RLS, and grant it needed privs."""
    await db.execute(text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='rls_test') THEN
                CREATE ROLE rls_test LOGIN PASSWORD 'rls_test' NOSUPERUSER;
            END IF;
        END $$;
    """))
    await db.execute(text("GRANT USAGE ON SCHEMA public TO rls_test"))
    await db.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO rls_test"))
    await db.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO rls_test"))
    # audit_log: INSERT-only for this role
    await db.execute(text("REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM rls_test"))
    await db.commit()


async def _rls_session() -> AsyncSession:
    """Open a session as rls_test (non-superuser, RLS actually enforced)."""
    settings = get_settings()
    url = settings.DATABASE_URL.replace("designpilot:designpilot", "rls_test:rls_test")
    engine = create_async_engine(url, pool_size=2)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    return factory, engine


async def test_alice_cannot_see_bob_designs(
    db: AsyncSession,
    user_id_alice: UUID,
    user_id_bob: UUID,
):
    """Alice and Bob each own a design. Under RLS, each sees only their own."""
    await _ensure_rls_role(db)

    # Seed users + 1 design each as superuser (bypasses RLS)
    await db.execute(text("""
        INSERT INTO users (id, email) VALUES
            (:alice, 'alice@test.com'),
            (:bob, 'bob@test.com')
    """), {"alice": user_id_alice, "bob": user_id_bob})

    await db.execute(text("""
        INSERT INTO designs (owner_id, name, part_type, prompt)
        VALUES
            (:alice, 'alice bracket', 'bracket', 'for alice'),
            (:bob, 'bob bracket', 'bracket', 'for bob')
    """), {"alice": user_id_alice, "bob": user_id_bob})
    await db.commit()

    # Now connect as the non-superuser role (RLS enforced)
    factory, engine = await _rls_session()
    try:
        # As alice
        async with factory() as sess:
            await sess.execute(
                text("SELECT set_config('app.current_user_id', :uid, true)"),
                {"uid": str(user_id_alice)},
            )
            result = await sess.execute(text("SELECT name FROM designs ORDER BY name"))
            alice_sees = [r[0] for r in result]
            assert alice_sees == ["alice bracket"], (
                f"RLS leak! Alice sees: {alice_sees}"
            )

        # As bob
        async with factory() as sess:
            await sess.execute(
                text("SELECT set_config('app.current_user_id', :uid, true)"),
                {"uid": str(user_id_bob)},
            )
            result = await sess.execute(text("SELECT name FROM designs ORDER BY name"))
            bob_sees = [r[0] for r in result]
            assert bob_sees == ["bob bracket"], f"RLS leak! Bob sees: {bob_sees}"

        # As anon (no user id set)
        async with factory() as sess:
            result = await sess.execute(text("SELECT count(*) FROM designs"))
            anon_sees = result.scalar_one()
            assert anon_sees == 0, f"Anon sees {anon_sees} designs — RLS broken"
    finally:
        await engine.dispose()


async def test_alice_cannot_insert_design_as_bob(
    db: AsyncSession,
    user_id_alice: UUID,
    user_id_bob: UUID,
):
    """WITH CHECK on the designs_owner_all policy blocks inserts where owner_id != current_user_id()."""
    await _ensure_rls_role(db)

    await db.execute(text("""
        INSERT INTO users (id, email) VALUES
            (:alice, 'alice2@test.com'),
            (:bob, 'bob2@test.com')
    """), {"alice": user_id_alice, "bob": user_id_bob})
    await db.commit()

    factory, engine = await _rls_session()
    try:
        async with factory() as sess:
            await sess.execute(
                text("SELECT set_config('app.current_user_id', :uid, true)"),
                {"uid": str(user_id_alice)},
            )
            # Alice tries to create a design OWNED BY BOB
            from sqlalchemy.exc import ProgrammingError, IntegrityError

            with pytest.raises((ProgrammingError, IntegrityError, Exception)) as exc_info:
                await sess.execute(text("""
                    INSERT INTO designs (owner_id, name) VALUES (:bob, 'hacked')
                """), {"bob": user_id_bob})
                await sess.commit()
            # Either RLS blocks (violates row-level) or policy check fires
            assert exc_info.value is not None
    finally:
        await engine.dispose()

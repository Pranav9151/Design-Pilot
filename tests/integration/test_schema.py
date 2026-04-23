"""
Integration: verify the migration actually created the v1.0 schema we promised.

These tests run against a real Postgres (the `apply_migrations` session
fixture applies the migration before this file runs).
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


pytestmark = [pytest.mark.integration]


EXPECTED_TABLES = {
    "users", "teams", "team_members", "roles",
    "designs", "design_diary", "design_feedback",
    "materials", "custom_materials",
    "knowledge_chunks", "audit_log",
}


async def test_all_tables_exist(db: AsyncSession):
    result = await db.execute(
        text(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname='public' AND tablename != 'alembic_version'"
        )
    )
    actual = {row[0] for row in result}
    missing = EXPECTED_TABLES - actual
    assert not missing, f"Missing tables: {missing}"


async def test_rls_enabled_on_user_data_tables(db: AsyncSession):
    """Every user-data table has RLS ENABLE and FORCE."""
    must_have_rls = [
        "users", "teams", "team_members",
        "designs", "design_diary", "design_feedback",
        "custom_materials", "knowledge_chunks",
    ]
    result = await db.execute(text("""
        SELECT relname, relrowsecurity, relforcerowsecurity
        FROM pg_class
        WHERE relkind='r'
          AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname='public')
          AND relname = ANY(:names)
    """), {"names": must_have_rls})
    rows = {r[0]: (r[1], r[2]) for r in result}
    for table in must_have_rls:
        assert table in rows, f"{table} not found"
        enabled, forced = rows[table]
        assert enabled, f"RLS not enabled on {table}"
        assert forced, f"RLS not FORCED on {table}"


async def test_system_roles_seeded(db: AsyncSession):
    """All 5 default roles exist as system roles (team_id IS NULL)."""
    from app.iam.permissions import DEFAULT_ROLES

    result = await db.execute(
        text("SELECT name, permissions FROM roles WHERE is_system=TRUE AND team_id IS NULL")
    )
    rows = {r[0]: set(r[1]) for r in result}

    for role_name, expected_perms in DEFAULT_ROLES.items():
        assert role_name in rows, f"System role '{role_name}' missing from DB"
        assert rows[role_name] == set(expected_perms), (
            f"System role '{role_name}' permissions drifted from code"
        )


async def test_audit_log_update_forbidden(db: AsyncSession):
    """Defense trigger raises on UPDATE even for superuser."""
    await db.execute(text(
        "INSERT INTO audit_log (id, action) VALUES (gen_random_uuid(), 'test.update')"
    ))
    await db.commit()

    # The trigger must raise, even with superuser privileges
    from sqlalchemy.exc import InternalError

    with pytest.raises((InternalError, Exception)) as exc_info:
        await db.execute(text("UPDATE audit_log SET action='hacked' WHERE action='test.update'"))
        await db.commit()

    assert "append-only" in str(exc_info.value).lower() or "forbidden" in str(exc_info.value).lower()


async def test_audit_log_delete_forbidden(db: AsyncSession):
    """Same protection against DELETE."""
    await db.execute(text(
        "INSERT INTO audit_log (id, action) VALUES (gen_random_uuid(), 'test.delete')"
    ))
    await db.commit()

    with pytest.raises(Exception) as exc_info:
        await db.execute(text("DELETE FROM audit_log WHERE action='test.delete'"))
        await db.commit()

    assert "append-only" in str(exc_info.value).lower() or "forbidden" in str(exc_info.value).lower()


async def test_pgvector_extension_installed(db: AsyncSession):
    result = await db.execute(text("SELECT 1 FROM pg_extension WHERE extname='vector'"))
    assert result.scalar_one_or_none() == 1


async def test_pgcrypto_extension_installed(db: AsyncSession):
    result = await db.execute(text("SELECT 1 FROM pg_extension WHERE extname='pgcrypto'"))
    assert result.scalar_one_or_none() == 1


async def test_knowledge_chunks_has_vector_column(db: AsyncSession):
    """Embedding column is vector(384), not TEXT."""
    result = await db.execute(text("""
        SELECT udt_name FROM information_schema.columns
        WHERE table_name='knowledge_chunks' AND column_name='embedding'
    """))
    assert result.scalar_one() == "vector"


async def test_designs_confidence_score_constraint(db: AsyncSession):
    """Confidence must be in [0, 100]."""
    # Need an owner first; bypass RLS via session setting
    await db.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": "11111111-1111-1111-1111-111111111111"},
    )
    await db.execute(text("""
        INSERT INTO users (id, email) VALUES
        ('11111111-1111-1111-1111-111111111111', 'ck@test.com')
    """))

    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await db.execute(text("""
            INSERT INTO designs (owner_id, confidence_score)
            VALUES ('11111111-1111-1111-1111-111111111111', 150)
        """))
        await db.commit()
    await db.rollback()


async def test_materials_poisson_ratio_constraint(db: AsyncSession):
    """Poisson's ratio must be in (0, 0.5) — physical reality."""
    from sqlalchemy.exc import IntegrityError

    bad_material = {
        "slug": "bad_material", "name": "Bad", "grade": "X", "category": "steel",
        "youngs_modulus_mpa": 200000, "yield_strength_mpa": 300,
        "ultimate_strength_mpa": 400, "density_kg_m3": 7800,
        "poissons_ratio": 0.7,  # invalid — must be < 0.5
        "elongation_percent": 10, "cte": 12, "thermal_conductivity": 50,
        "max_service_temp_c": 400, "machinability_rating": 70,
        "cost_per_kg_usd": 1.0, "source": "test",
    }

    with pytest.raises(IntegrityError):
        await db.execute(
            text("""
                INSERT INTO materials (slug, name, grade, category,
                    youngs_modulus_mpa, yield_strength_mpa, ultimate_strength_mpa,
                    density_kg_m3, poissons_ratio, elongation_percent,
                    cte, thermal_conductivity, max_service_temp_c,
                    machinability_rating, cost_per_kg_usd, source)
                VALUES (:slug, :name, :grade, :category,
                    :youngs_modulus_mpa, :yield_strength_mpa, :ultimate_strength_mpa,
                    :density_kg_m3, :poissons_ratio, :elongation_percent,
                    :cte, :thermal_conductivity, :max_service_temp_c,
                    :machinability_rating, :cost_per_kg_usd, :source)
            """),
            bad_material,
        )
        await db.commit()
    await db.rollback()

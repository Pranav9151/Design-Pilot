"""v1.0 initial schema — IAM, designs, materials, knowledge, audit + RLS

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-17 00:00:00.000000

This migration sets up the complete v1.0 schema:
  - Extensions (pgcrypto for gen_random_uuid, pgvector for RAG)
  - All tables: users, teams, team_members, roles,
                designs, design_diary, design_feedback,
                materials, custom_materials,
                knowledge_chunks, audit_log
  - Row-Level Security (RLS) policies on every user-data table
  - Audit log immutability (REVOKE UPDATE/DELETE)
  - Seeded system roles (owner, admin, engineer, reviewer, viewer)
  - Indexes for the most common query patterns

All tables use UUID primary keys. All timestamps are TIMESTAMPTZ.
All JSONB defaults are '{}' or '[]'.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.iam.permissions import DEFAULT_ROLES

# revision identifiers
revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ════════════════════════════════════════════════════════════════
    # Extensions
    # ════════════════════════════════════════════════════════════════
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
    # pgvector is available on Supabase and we'll install it in local docker-compose
    op.execute('CREATE EXTENSION IF NOT EXISTS "vector"')

    # ════════════════════════════════════════════════════════════════
    # users
    # ════════════════════════════════════════════════════════════════
    op.execute("""
        CREATE TABLE users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email VARCHAR(255) UNIQUE NOT NULL,
            name VARCHAR(255),
            plan VARCHAR(50) NOT NULL DEFAULT 'free',
            preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ════════════════════════════════════════════════════════════════
    # teams
    # ════════════════════════════════════════════════════════════════
    op.execute("""
        CREATE TABLE teams (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(255) NOT NULL,
            slug VARCHAR(100) UNIQUE NOT NULL,
            plan VARCHAR(50) NOT NULL DEFAULT 'team',
            settings JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.create_index("ix_teams_slug", "teams", ["slug"], unique=True)

    # ════════════════════════════════════════════════════════════════
    # roles — team_id NULL = system role (shared across every workspace)
    # ════════════════════════════════════════════════════════════════
    op.execute("""
        CREATE TABLE roles (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            team_id UUID REFERENCES teams(id) ON DELETE CASCADE,
            name VARCHAR(100) NOT NULL,
            is_system BOOLEAN NOT NULL DEFAULT FALSE,
            permissions TEXT[] NOT NULL DEFAULT '{}',
            description TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_roles_team_name UNIQUE (team_id, name)
        )
    """)

    # ════════════════════════════════════════════════════════════════
    # team_members
    # ════════════════════════════════════════════════════════════════
    op.execute("""
        CREATE TABLE team_members (
            team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role_id UUID REFERENCES roles(id) ON DELETE SET NULL,
            invited_by UUID REFERENCES users(id) ON DELETE SET NULL,
            joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (team_id, user_id)
        )
    """)
    op.create_index("ix_team_members_user_id", "team_members", ["user_id"])

    # ════════════════════════════════════════════════════════════════
    # materials (canonical)
    # ════════════════════════════════════════════════════════════════
    op.execute("""
        CREATE TABLE materials (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            slug VARCHAR(100) UNIQUE NOT NULL,
            name VARCHAR(100) NOT NULL,
            grade VARCHAR(50) NOT NULL,
            category VARCHAR(50) NOT NULL,
            youngs_modulus_mpa DOUBLE PRECISION NOT NULL,
            yield_strength_mpa DOUBLE PRECISION NOT NULL,
            ultimate_strength_mpa DOUBLE PRECISION NOT NULL,
            density_kg_m3 DOUBLE PRECISION NOT NULL,
            poissons_ratio DOUBLE PRECISION NOT NULL,
            elongation_percent DOUBLE PRECISION NOT NULL,
            cte DOUBLE PRECISION NOT NULL,
            thermal_conductivity DOUBLE PRECISION NOT NULL,
            max_service_temp_c DOUBLE PRECISION NOT NULL,
            machinability_rating DOUBLE PRECISION NOT NULL,
            cost_per_kg_usd DOUBLE PRECISION NOT NULL,
            source TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_materials_e_positive CHECK (youngs_modulus_mpa > 0),
            CONSTRAINT ck_materials_yield_positive CHECK (yield_strength_mpa > 0),
            CONSTRAINT ck_materials_density_positive CHECK (density_kg_m3 > 0),
            CONSTRAINT ck_materials_poisson_range CHECK (poissons_ratio > 0 AND poissons_ratio < 0.5),
            CONSTRAINT ck_materials_machinability_range CHECK (
                machinability_rating >= 0 AND machinability_rating <= 100
            )
        )
    """)
    op.create_index("ix_materials_slug", "materials", ["slug"], unique=True)
    op.create_index("ix_materials_category", "materials", ["category"])

    # ════════════════════════════════════════════════════════════════
    # custom_materials (v1.5; schema in place now)
    # ════════════════════════════════════════════════════════════════
    op.execute("""
        CREATE TABLE custom_materials (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
            created_by UUID REFERENCES users(id) ON DELETE SET NULL,
            slug VARCHAR(100) NOT NULL,
            name VARCHAR(100) NOT NULL,
            grade VARCHAR(50) NOT NULL,
            category VARCHAR(50) NOT NULL,
            youngs_modulus_mpa DOUBLE PRECISION NOT NULL,
            yield_strength_mpa DOUBLE PRECISION NOT NULL,
            ultimate_strength_mpa DOUBLE PRECISION NOT NULL,
            density_kg_m3 DOUBLE PRECISION NOT NULL,
            poissons_ratio DOUBLE PRECISION NOT NULL,
            elongation_percent DOUBLE PRECISION NOT NULL,
            cte DOUBLE PRECISION NOT NULL,
            thermal_conductivity DOUBLE PRECISION NOT NULL,
            max_service_temp_c DOUBLE PRECISION NOT NULL,
            machinability_rating DOUBLE PRECISION NOT NULL,
            cost_per_kg_usd DOUBLE PRECISION NOT NULL,
            source TEXT NOT NULL,
            approved BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_custom_materials_team_slug UNIQUE (team_id, slug)
        )
    """)
    op.create_index("ix_custom_materials_team_id", "custom_materials", ["team_id"])

    # ════════════════════════════════════════════════════════════════
    # designs
    # ════════════════════════════════════════════════════════════════
    op.execute("""
        CREATE TABLE designs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            team_id UUID REFERENCES teams(id) ON DELETE SET NULL,
            name VARCHAR(255),
            part_type VARCHAR(100) NOT NULL DEFAULT 'bracket',
            prompt TEXT,
            status VARCHAR(50) NOT NULL DEFAULT 'draft',

            cadquery_code TEXT,
            parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
            step_url VARCHAR(500),
            glb_url VARCHAR(500),

            lock1_results JSONB,
            lock2_results JSONB,
            lock3_results JSONB,
            confidence_score DOUBLE PRECISION,
            confidence_explanation TEXT,

            material_id UUID REFERENCES materials(id) ON DELETE RESTRICT,
            simulation JSONB,
            dfm JSONB,
            cost JSONB,
            assumptions JSONB NOT NULL DEFAULT '[]'::jsonb,

            version INTEGER NOT NULL DEFAULT 1,
            parent_id UUID REFERENCES designs(id) ON DELETE SET NULL,
            tags TEXT[] NOT NULL DEFAULT '{}',

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_designs_confidence_range CHECK (
                confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 100)
            ),
            CONSTRAINT ck_designs_status_enum CHECK (
                status IN ('draft','generated','analyzed','finalized','archived','failed')
            )
        )
    """)
    op.create_index("ix_designs_owner_id", "designs", ["owner_id"])
    op.create_index("ix_designs_team_id", "designs", ["team_id"])
    op.create_index("ix_designs_created_at_desc", "designs", [sa.text("created_at DESC")])

    # ════════════════════════════════════════════════════════════════
    # design_diary
    # ════════════════════════════════════════════════════════════════
    op.execute("""
        CREATE TABLE design_diary (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            design_id UUID NOT NULL REFERENCES designs(id) ON DELETE CASCADE,
            user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            entry_type VARCHAR(100) NOT NULL,
            snapshot JSONB,
            note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.create_index("ix_design_diary_design_id", "design_diary", ["design_id"])
    op.create_index("ix_design_diary_created_at", "design_diary", ["created_at"])

    # ════════════════════════════════════════════════════════════════
    # design_feedback
    # ════════════════════════════════════════════════════════════════
    op.execute("""
        CREATE TABLE design_feedback (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            design_id UUID NOT NULL REFERENCES designs(id) ON DELETE CASCADE,
            user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            rating INTEGER,
            verdict VARCHAR(50) NOT NULL,
            comment TEXT,
            structured JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_design_feedback_rating_range CHECK (
                rating IS NULL OR (rating >= 1 AND rating <= 5)
            ),
            CONSTRAINT ck_design_feedback_verdict_enum CHECK (
                verdict IN ('approved','rejected','modified')
            )
        )
    """)
    op.create_index("ix_design_feedback_design_id", "design_feedback", ["design_id"])

    # ════════════════════════════════════════════════════════════════
    # knowledge_chunks (pgvector embedding column)
    # ════════════════════════════════════════════════════════════════
    op.execute("""
        CREATE TABLE knowledge_chunks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID REFERENCES users(id) ON DELETE CASCADE,
            team_id UUID REFERENCES teams(id) ON DELETE CASCADE,
            design_id UUID REFERENCES designs(id) ON DELETE CASCADE,
            source_type VARCHAR(50) NOT NULL,
            source_ref VARCHAR(500) NOT NULL,
            title VARCHAR(500),
            content TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            tokens INTEGER,
            embedding vector(384),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.create_index("ix_knowledge_chunks_user_id", "knowledge_chunks", ["user_id"])
    op.create_index("ix_knowledge_chunks_team_id", "knowledge_chunks", ["team_id"])
    op.create_index("ix_knowledge_chunks_design_id", "knowledge_chunks", ["design_id"])
    op.create_index("ix_knowledge_chunks_source_type", "knowledge_chunks", ["source_type"])
    # IVFFlat index on embedding for cosine-distance search (v1.0 uses L2; revisit at scale)
    op.execute("""
        CREATE INDEX ix_knowledge_chunks_embedding
        ON knowledge_chunks
        USING ivfflat (embedding vector_l2_ops)
        WITH (lists = 100)
    """)

    # ════════════════════════════════════════════════════════════════
    # audit_log (append-only; UPDATE/DELETE revoked below)
    # ════════════════════════════════════════════════════════════════
    op.execute("""
        CREATE TABLE audit_log (
            id UUID PRIMARY KEY,
            actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            team_id UUID REFERENCES teams(id) ON DELETE SET NULL,
            action VARCHAR(200) NOT NULL,
            resource_type VARCHAR(100),
            resource_id VARCHAR(500),
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            ip_address VARCHAR(64),
            user_agent TEXT,
            status_code INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.create_index("ix_audit_log_actor_user_id", "audit_log", ["actor_user_id"])
    op.create_index("ix_audit_log_team_id", "audit_log", ["team_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_resource_type", "audit_log", ["resource_type"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])

    # ════════════════════════════════════════════════════════════════
    # Audit log IMMUTABILITY: revoke UPDATE/DELETE from all app roles.
    # The only way to add rows is INSERT; the only way to remove is
    # a privileged SUPERUSER retention job running outside the app.
    # ════════════════════════════════════════════════════════════════
    op.execute("""
        DO $$
        BEGIN
            -- revoke from 'authenticated' + 'anon' + 'service_role' (Supabase)
            -- and from 'public' for non-Supabase environments.
            EXECUTE 'REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM PUBLIC';
            -- Attempt Supabase-specific revokes; ignore if roles don't exist.
            BEGIN
                EXECUTE 'REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM authenticated';
            EXCEPTION WHEN undefined_object THEN NULL;
            END;
            BEGIN
                EXECUTE 'REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM anon';
            EXCEPTION WHEN undefined_object THEN NULL;
            END;
        END $$;
    """)

    # Defense-in-depth: a trigger that raises on any UPDATE/DELETE, even
    # if some future privileged role ever gains the rights.
    op.execute("""
        CREATE OR REPLACE FUNCTION prevent_audit_log_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only; % is forbidden', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_audit_log_no_update
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_mutation();
    """)

    # ════════════════════════════════════════════════════════════════
    # Row-Level Security
    #
    # In v1.0 we assume Supabase. The Supabase `authenticated` role
    # sets `auth.uid()` from the JWT claim. We write policies against
    # that. For non-Supabase local dev, an override SET LOCAL can be
    # used in session fixtures.
    # ════════════════════════════════════════════════════════════════
    for table in [
        "users", "teams", "team_members", "designs", "design_diary",
        "design_feedback", "custom_materials", "knowledge_chunks",
    ]:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # Helper: the auth.uid() function may not exist outside Supabase.
    # We create a wrapper that falls back to a session-level setting
    # `app.current_user_id` so local dev/tests work without Supabase.
    op.execute("""
        CREATE OR REPLACE FUNCTION current_user_id() RETURNS UUID AS $$
        DECLARE
            uid UUID;
        BEGIN
            -- Prefer Supabase auth.uid() when available
            BEGIN
                SELECT auth.uid() INTO uid;
            EXCEPTION WHEN OTHERS THEN
                uid := NULL;
            END;
            -- Fall back to session setting for local dev/tests
            IF uid IS NULL THEN
                BEGIN
                    uid := NULLIF(current_setting('app.current_user_id', TRUE), '')::UUID;
                EXCEPTION WHEN OTHERS THEN
                    uid := NULL;
                END;
            END IF;
            RETURN uid;
        END;
        $$ LANGUAGE plpgsql STABLE;
    """)

    # users — user can only see/update their own row
    op.execute("""
        CREATE POLICY users_self_read ON users
            FOR SELECT USING (id = current_user_id())
    """)
    op.execute("""
        CREATE POLICY users_self_update ON users
            FOR UPDATE USING (id = current_user_id())
    """)

    # designs — owner OR (future) team member
    op.execute("""
        CREATE POLICY designs_owner_all ON designs
            FOR ALL USING (owner_id = current_user_id())
            WITH CHECK (owner_id = current_user_id())
    """)
    op.execute("""
        CREATE POLICY designs_team_read ON designs
            FOR SELECT USING (
                team_id IS NOT NULL AND team_id IN (
                    SELECT team_id FROM team_members WHERE user_id = current_user_id()
                )
            )
    """)

    # design_diary — follows design ownership
    op.execute("""
        CREATE POLICY design_diary_via_design ON design_diary
            FOR ALL USING (
                EXISTS (
                    SELECT 1 FROM designs d
                    WHERE d.id = design_diary.design_id
                    AND (d.owner_id = current_user_id() OR d.team_id IN (
                        SELECT team_id FROM team_members WHERE user_id = current_user_id()
                    ))
                )
            )
    """)

    # design_feedback — same as diary
    op.execute("""
        CREATE POLICY design_feedback_via_design ON design_feedback
            FOR ALL USING (
                EXISTS (
                    SELECT 1 FROM designs d
                    WHERE d.id = design_feedback.design_id
                    AND (d.owner_id = current_user_id() OR d.team_id IN (
                        SELECT team_id FROM team_members WHERE user_id = current_user_id()
                    ))
                )
            )
    """)

    # knowledge_chunks — public (all NULLs) OR own OR team
    op.execute("""
        CREATE POLICY kb_read ON knowledge_chunks
            FOR SELECT USING (
                (user_id IS NULL AND team_id IS NULL)
                OR user_id = current_user_id()
                OR team_id IN (SELECT team_id FROM team_members WHERE user_id = current_user_id())
            )
    """)
    op.execute("""
        CREATE POLICY kb_write_own ON knowledge_chunks
            FOR ALL USING (user_id = current_user_id())
            WITH CHECK (user_id = current_user_id())
    """)

    # teams — members can read; writes in v1.5 via route-level permission checks
    op.execute("""
        CREATE POLICY teams_member_read ON teams
            FOR SELECT USING (
                id IN (SELECT team_id FROM team_members WHERE user_id = current_user_id())
            )
    """)
    op.execute("""
        CREATE POLICY team_members_self_read ON team_members
            FOR SELECT USING (
                user_id = current_user_id() OR team_id IN (
                    SELECT team_id FROM team_members WHERE user_id = current_user_id()
                )
            )
    """)

    # custom_materials — visible to team members
    op.execute("""
        CREATE POLICY custom_materials_team ON custom_materials
            FOR ALL USING (
                team_id IN (SELECT team_id FROM team_members WHERE user_id = current_user_id())
            )
    """)

    # ════════════════════════════════════════════════════════════════
    # Seed system roles (owner / admin / engineer / reviewer / viewer)
    # team_id = NULL indicates these are global system roles.
    # ════════════════════════════════════════════════════════════════
    conn = op.get_bind()
    for name, perms in DEFAULT_ROLES.items():
        conn.execute(
            sa.text("""
                INSERT INTO roles (team_id, name, is_system, permissions, description)
                VALUES (NULL, :name, TRUE, :perms, :desc)
            """),
            {
                "name": name,
                "perms": perms,
                "desc": f"System role: {name}",
            },
        )


def downgrade() -> None:
    # Drop in reverse dependency order
    op.execute("DROP TABLE IF EXISTS audit_log CASCADE")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_log_mutation() CASCADE")
    op.execute("DROP TABLE IF EXISTS knowledge_chunks CASCADE")
    op.execute("DROP TABLE IF EXISTS design_feedback CASCADE")
    op.execute("DROP TABLE IF EXISTS design_diary CASCADE")
    op.execute("DROP TABLE IF EXISTS designs CASCADE")
    op.execute("DROP TABLE IF EXISTS custom_materials CASCADE")
    op.execute("DROP TABLE IF EXISTS materials CASCADE")
    op.execute("DROP TABLE IF EXISTS team_members CASCADE")
    op.execute("DROP TABLE IF EXISTS roles CASCADE")
    op.execute("DROP TABLE IF EXISTS teams CASCADE")
    op.execute("DROP TABLE IF EXISTS users CASCADE")
    op.execute("DROP FUNCTION IF EXISTS current_user_id() CASCADE")
    # Leave extensions in place — dropping pgvector / pgcrypto may affect
    # other schemas on shared databases.

"""add stripe_customer_id to users

Revision ID: 0003_stripe_customer_id
Revises: 0002_fix_rls_team_members_recursion
Create Date: 2026-04-24

Adds:
  - users.stripe_customer_id   VARCHAR(100) NULLABLE  (Stripe cust_xxx ID)
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_stripe_customer_id"
down_revision = "0002_fix_rls_team_members_recursion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("stripe_customer_id", sa.String(100), nullable=True),
    )
    op.create_index(
        "ix_users_stripe_customer_id",
        "users",
        ["stripe_customer_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_users_stripe_customer_id", table_name="users")
    op.drop_column("users", "stripe_customer_id")

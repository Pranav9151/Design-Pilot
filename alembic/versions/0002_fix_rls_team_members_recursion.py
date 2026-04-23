"""fix RLS infinite recursion on team_members

Revision ID: 0002_fix_tm_recursion
Revises: 0001_initial
Create Date: 2026-04-18 00:00:00.000000

The original team_members_self_read policy does:
    USING (
      user_id = current_user_id()
      OR team_id IN (SELECT team_id FROM team_members WHERE user_id = current_user_id())
    )

That second OR branch reads team_members from within team_members' own policy,
which Postgres flags as infinite recursion the moment any other policy references
team_members (e.g. designs_team_read).

Fix: simplify team_members to "you can see rows where you are the member".
Cross-user team visibility is still enforced via designs_team_read etc. which
subquery team_members directly. For v1.0 (solo accounts) this is strictly
no-functional-change because teams are unused; the policy is still correct for
v1.5 teams because you only need to see your OWN team_members rows to know
which teams you belong to.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_fix_tm_recursion"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop the recursive policy
    op.execute("DROP POLICY IF EXISTS team_members_self_read ON team_members")

    # Replace with a non-recursive version.
    # A user can see their own team_members rows. Cross-user team visibility
    # (e.g. "all members of a team I'm in") will be handled in v1.5 via a
    # SECURITY DEFINER helper function that is itself exempt from RLS.
    op.execute("""
        CREATE POLICY team_members_self_read ON team_members
            FOR SELECT USING (user_id = current_user_id())
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS team_members_self_read ON team_members")
    op.execute("""
        CREATE POLICY team_members_self_read ON team_members
            FOR SELECT USING (
                user_id = current_user_id() OR team_id IN (
                    SELECT team_id FROM team_members WHERE user_id = current_user_id()
                )
            )
    """)

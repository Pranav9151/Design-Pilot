"""
ORM models for DesignPilot MECH v1.0.

Schema is IAM-complete from day 1 (users, teams, team_members, roles),
even though v1.0 only uses solo accounts. v1.5 unlocks team features
without any schema changes.

All user-data tables have RLS enabled at the DB level (see first
Alembic migration). Application-layer checks (require_permission)
are defense-in-depth on top of DB-level isolation.
"""
from app.models.audit import AuditLog
from app.models.design import Design, DesignDiary, DesignFeedback
from app.models.knowledge import KnowledgeChunk
from app.models.material import CustomMaterial, Material
from app.models.user import Role, Team, TeamMember, User

__all__ = [
    "AuditLog",
    "CustomMaterial",
    "Design",
    "DesignDiary",
    "DesignFeedback",
    "KnowledgeChunk",
    "Material",
    "Role",
    "Team",
    "TeamMember",
    "User",
]

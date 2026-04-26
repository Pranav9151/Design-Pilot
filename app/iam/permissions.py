"""
Permissions & default roles for IAM.

This is the complete permission catalog from the architecture document
(Pillar 4, Security + IAM). In v1.0, only the 'owner' role is exposed
(solo accounts), but the full grid is schema-enforced from day 1 so
v1.5 can enable teams without migrations.

DO NOT add permissions ad-hoc in feature code. Every new permission
must be added here AND to the seed data for DEFAULT_ROLES.
"""
from __future__ import annotations

from enum import StrEnum


class Permission(StrEnum):
    """Canonical permission identifiers. Values are stored as strings in DB."""

    # ── Design ──────────────────────────────────────────────
    DESIGN_CREATE = "design.create"
    DESIGN_VIEW_OWN = "design.view.own"
    DESIGN_VIEW_TEAM = "design.view.team"
    DESIGN_VIEW_ANY = "design.view.any"
    DESIGN_EDIT_OWN = "design.edit.own"
    DESIGN_EDIT_TEAM = "design.edit.team"
    DESIGN_EDIT_ANY = "design.edit.any"
    DESIGN_DELETE_OWN = "design.delete.own"
    DESIGN_DELETE_TEAM = "design.delete.team"
    DESIGN_DELETE_ANY = "design.delete.any"
    DESIGN_EXPORT_STEP = "design.export.step"
    DESIGN_EXPORT_STL = "design.export.stl"
    DESIGN_EXPORT_DRAWING = "design.export.drawing"
    DESIGN_EXPORT_BOM = "design.export.bom"
    DESIGN_SHARE_PUBLIC = "design.share.public"
    DESIGN_SHARE_TEAM = "design.share.team"
    DESIGN_FORK = "design.fork"
    DESIGN_COMMENT = "design.comment"

    # ── AI features ─────────────────────────────────────────
    AI_PROMPT = "ai.prompt"
    AI_OPTIMIZE = "ai.optimize"
    AI_RECOMMEND_MATERIAL = "ai.recommend_material"
    AI_EXPLAIN = "ai.explain"
    AI_DESIGN_REVIEW = "ai.design_review"
    AI_BULK_GENERATE = "ai.bulk_generate"

    # ── Materials ───────────────────────────────────────────
    MATERIAL_VIEW = "material.view"
    MATERIAL_CREATE = "material.create"
    MATERIAL_EDIT = "material.edit"

    # ── Team management ────────────────────────────────────
    TEAM_VIEW = "team.view"
    TEAM_MEMBERS_INVITE = "team.members.invite"
    TEAM_MEMBERS_REMOVE = "team.members.remove"
    TEAM_ROLES_VIEW = "team.roles.view"
    TEAM_ROLES_CREATE = "team.roles.create"
    TEAM_ROLES_EDIT = "team.roles.edit"
    TEAM_BILLING_VIEW = "team.billing.view"
    TEAM_BILLING_EDIT = "team.billing.edit"

    # ── Knowledge base ──────────────────────────────────────
    KB_SEARCH_PERSONAL = "kb.search.personal"
    KB_SEARCH_TEAM = "kb.search.team"
    KB_SEARCH_PUBLIC = "kb.search.public"
    KB_CONTRIBUTE_TEAM = "kb.contribute.team"
    KB_ADMIN_TEAM = "kb.admin.team"

    # ── Integrations ────────────────────────────────────────
    INTEGRATION_XOMETRY = "integration.xometry"
    INTEGRATION_PROTOLABS = "integration.protolabs"
    INTEGRATION_CUSTOM_API = "integration.custom_api"

    # ── System / admin ─────────────────────────────────────
    SYSTEM_AUDIT_VIEW = "system.audit.view"
    SYSTEM_SETTINGS_EDIT = "system.settings.edit"
    SYSTEM_DFM_RULES_EDIT = "system.dfm_rules.edit"


# Frozen set of all permission strings for fast membership checks.
PERMISSIONS: frozenset[str] = frozenset(p.value for p in Permission)


# ── Default role bundles ───────────────────────────────────────
# Every new team gets these roles seeded automatically.
# In v1.0, only 'owner' is assigned (solo accounts).

_ALL_PERMS: list[str] = [p.value for p in Permission]

_ADMIN_PERMS: list[str] = [
    p for p in _ALL_PERMS
    if not p.startswith("team.billing")
]

_ENGINEER_PERMS: list[str] = [
    Permission.DESIGN_CREATE,
    Permission.DESIGN_VIEW_OWN,
    Permission.DESIGN_VIEW_TEAM,
    Permission.DESIGN_EDIT_OWN,
    Permission.DESIGN_DELETE_OWN,
    Permission.DESIGN_EXPORT_STEP,
    Permission.DESIGN_EXPORT_STL,
    Permission.DESIGN_EXPORT_DRAWING,
    Permission.DESIGN_EXPORT_BOM,
    Permission.DESIGN_SHARE_PUBLIC,
    Permission.DESIGN_SHARE_TEAM,
    Permission.DESIGN_FORK,
    Permission.DESIGN_COMMENT,
    Permission.AI_PROMPT,
    Permission.AI_OPTIMIZE,
    Permission.AI_RECOMMEND_MATERIAL,
    Permission.AI_EXPLAIN,
    Permission.MATERIAL_VIEW,
    Permission.KB_SEARCH_PERSONAL,
    Permission.KB_SEARCH_TEAM,
    Permission.KB_SEARCH_PUBLIC,
    Permission.KB_CONTRIBUTE_TEAM,
    Permission.INTEGRATION_XOMETRY,
    Permission.INTEGRATION_PROTOLABS,
]

_REVIEWER_PERMS: list[str] = [
    Permission.DESIGN_VIEW_TEAM,
    Permission.DESIGN_COMMENT,
    Permission.DESIGN_EXPORT_STEP,
    Permission.DESIGN_EXPORT_DRAWING,
    Permission.MATERIAL_VIEW,
    Permission.KB_SEARCH_TEAM,
]

_VIEWER_PERMS: list[str] = [
    Permission.DESIGN_VIEW_TEAM,
    Permission.MATERIAL_VIEW,
    Permission.KB_SEARCH_TEAM,
]


DEFAULT_ROLES: dict[str, list[str]] = {
    "owner": _ALL_PERMS,
    "admin": _ADMIN_PERMS,
    "engineer": _ENGINEER_PERMS,
    "reviewer": _REVIEWER_PERMS,
    "viewer": _VIEWER_PERMS,
}


def is_valid_permission(p: str) -> bool:
    """Return True if the given string is a known permission."""
    return p in PERMISSIONS


def role_permissions(role_name: str) -> list[str]:
    """Return the permission list for a default role. Raises KeyError if unknown."""
    return list(DEFAULT_ROLES[role_name])

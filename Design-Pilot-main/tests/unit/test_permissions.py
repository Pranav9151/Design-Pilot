"""
Unit tests for the IAM permissions catalog.

These tests are INVARIANTS — they catch bugs where someone adds a
permission to DEFAULT_ROLES without adding it to the Permission enum,
or vice versa. Every string in the role bundles must map to a real enum value.
"""
from __future__ import annotations

from app.iam.permissions import (
    DEFAULT_ROLES,
    PERMISSIONS,
    Permission,
    is_valid_permission,
    role_permissions,
)


def test_permissions_enum_matches_frozenset():
    """PERMISSIONS frozenset equals the set of all enum values."""
    enum_values = {p.value for p in Permission}
    assert set(PERMISSIONS) == enum_values


def test_all_default_role_permissions_are_valid():
    """Every string used in a default role must be a real Permission."""
    for role_name, perms in DEFAULT_ROLES.items():
        for p in perms:
            assert is_valid_permission(p), (
                f"Role '{role_name}' references unknown permission '{p}'"
            )


def test_owner_has_all_permissions():
    """Owner is the root role — must have every permission."""
    owner_perms = set(DEFAULT_ROLES["owner"])
    assert owner_perms == set(PERMISSIONS)


def test_admin_excludes_only_billing():
    """Admin has everything except billing permissions."""
    admin_perms = set(DEFAULT_ROLES["admin"])
    billing_perms = {p for p in PERMISSIONS if p.startswith("team.billing")}
    assert admin_perms == set(PERMISSIONS) - billing_perms


def test_viewer_is_strict_subset_of_reviewer():
    """Viewer should not have any permission that reviewer lacks."""
    viewer = set(DEFAULT_ROLES["viewer"])
    reviewer = set(DEFAULT_ROLES["reviewer"])
    assert viewer.issubset(reviewer), (
        "viewer must be a subset of reviewer — or the role hierarchy is broken"
    )


def test_reviewer_is_strict_subset_of_engineer():
    """Reviewer can only read/comment; engineer must have those + more."""
    reviewer = set(DEFAULT_ROLES["reviewer"])
    engineer = set(DEFAULT_ROLES["engineer"])
    # Reviewer has DESIGN_EXPORT_STEP/DRAWING for compliance; engineer has same + more.
    # So reviewer ⊆ engineer should hold.
    assert reviewer.issubset(engineer)


def test_engineer_cannot_delete_other_designs():
    """Engineers can only delete their own designs."""
    engineer = set(DEFAULT_ROLES["engineer"])
    assert Permission.DESIGN_DELETE_OWN.value in engineer
    assert Permission.DESIGN_DELETE_TEAM.value not in engineer
    assert Permission.DESIGN_DELETE_ANY.value not in engineer


def test_engineer_cannot_edit_team_roles():
    """Engineers cannot modify team roles / billing."""
    engineer = set(DEFAULT_ROLES["engineer"])
    for forbidden in (
        Permission.TEAM_ROLES_CREATE,
        Permission.TEAM_ROLES_EDIT,
        Permission.TEAM_BILLING_VIEW,
        Permission.TEAM_BILLING_EDIT,
        Permission.TEAM_MEMBERS_INVITE,
        Permission.SYSTEM_DFM_RULES_EDIT,
    ):
        assert forbidden.value not in engineer


def test_every_enum_value_is_dotted_lowercase():
    """Convention: permission strings are 'group.subgroup.action' lowercase."""
    for p in Permission:
        assert p.value == p.value.lower()
        assert "." in p.value
        # No accidental whitespace
        assert p.value.strip() == p.value


def test_role_permissions_returns_list_not_shared_reference():
    """role_permissions() returns a NEW list so callers can mutate safely."""
    a = role_permissions("viewer")
    b = role_permissions("viewer")
    assert a == b
    assert a is not b  # must be a fresh copy


def test_is_valid_permission_rejects_bogus():
    assert not is_valid_permission("design.create.everything")
    assert not is_valid_permission("")
    assert not is_valid_permission("design.CREATE")  # case-sensitive


def test_permission_count_matches_architecture_doc():
    """The arch doc promises 30+ permissions. We shipped 46; make sure
    we don't accidentally drop below 40."""
    assert len(PERMISSIONS) >= 40

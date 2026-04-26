"""
Identity & Access Management (IAM).

Full schema built from day 1 per architecture contract,
even though v1.0 only exposes the 'owner' role (solo accounts).
v1.5 enables teams and the full permission system.
"""
from app.iam.permissions import DEFAULT_ROLES, PERMISSIONS, Permission
from app.iam.deps import require_permission

__all__ = ["DEFAULT_ROLES", "PERMISSIONS", "Permission", "require_permission"]

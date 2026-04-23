"""
IAM dependencies for FastAPI routes.

Two primitives:
- `get_current_user()` — validates JWT, returns User
- `require_permission(p)` — dependency factory that checks permission

Usage:
    @router.post("/designs", dependencies=[Depends(require_permission(Permission.DESIGN_CREATE))])
    async def create_design(...): ...

In v1.0, every authenticated user has the 'owner' role (implicit),
so all permissions are granted. The check is still enforced so that
v1.5 can add team members without any route-code changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.core.config import Settings, get_settings
from app.iam.permissions import DEFAULT_ROLES, Permission


bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    """Authenticated user derived from a validated JWT."""

    id: UUID
    email: str
    # In v1.0 every user has 'owner' role on their own account (solo).
    # In v1.5, permissions derive from team_members.role_id -> roles.permissions.
    permissions: frozenset[str]
    # The 'sub' claim (Supabase user id) equals `id`.
    raw_claims: dict


class AuthenticationError(HTTPException):
    def __init__(self, detail: str = "Could not validate credentials"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


class PermissionDenied(HTTPException):
    def __init__(self, missing: str):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing required permission: {missing}",
        )


async def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    """Decode and validate the Supabase JWT from Authorization header."""
    if creds is None or not creds.credentials:
        raise AuthenticationError("Missing bearer token")

    token = creds.credentials

    try:
        payload = jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=[settings.SUPABASE_JWT_ALGORITHM],
            audience=settings.SUPABASE_JWT_AUDIENCE,
        )
    except JWTError as exc:
        raise AuthenticationError(f"Invalid token: {exc}") from exc

    user_id_raw = payload.get("sub")
    if not user_id_raw:
        raise AuthenticationError("Token missing 'sub' claim")

    try:
        user_id = UUID(user_id_raw)
    except (ValueError, TypeError) as exc:
        raise AuthenticationError("Invalid user id in token") from exc

    email = payload.get("email", "")

    # v1.0: every authenticated user implicitly owns their workspace
    # and gets the full 'owner' permission bundle. v1.5 will replace
    # this with a DB lookup against team_members.role_id -> roles.permissions.
    perms = frozenset(DEFAULT_ROLES["owner"])

    # Store user_id in request state so audit middleware can pick it up
    request.state.user_id = str(user_id)

    return CurrentUser(
        id=user_id,
        email=email,
        permissions=perms,
        raw_claims=payload,
    )


def require_permission(permission: Permission | str):
    """
    Dependency factory: returns a dependency that raises 403
    if the current user lacks the specified permission.
    """
    perm_str = permission.value if isinstance(permission, Permission) else str(permission)

    async def _check(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if perm_str not in user.permissions:
            raise PermissionDenied(perm_str)
        return user

    # Name the function for clearer OpenAPI docs
    _check.__name__ = f"require_{perm_str.replace('.', '_')}"
    return _check

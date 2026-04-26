"""
User & Team ORM models.

v1.0 exposes only solo accounts (team_id=NULL on designs). The full
IAM schema is in place so v1.5 can enable teams by adding rows to
`teams` and `team_members` — no migrations required.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # Supabase Auth user id. Mirrors id; kept explicit for clarity.
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Pricing plan (india pricing from GTM: free / solo / team)
    plan: Mapped[str] = mapped_column(String(50), nullable=False, default="free")
    stripe_customer_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    # Arbitrary user preferences (UI, units, shortcuts, etc.)
    preferences: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    designs: Mapped[list["Design"]] = relationship(  # noqa: F821
        "Design", back_populates="owner", cascade="all, delete-orphan", foreign_keys="Design.owner_id"
    )
    team_memberships: Mapped[list["TeamMember"]] = relationship(
        "TeamMember", back_populates="user", cascade="all, delete-orphan", foreign_keys="TeamMember.user_id"
    )

    def __repr__(self) -> str:
        return f"<User {self.email}>"


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    plan: Mapped[str] = mapped_column(String(50), nullable=False, default="team")
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    members: Mapped[list["TeamMember"]] = relationship(
        "TeamMember", back_populates="team", cascade="all, delete-orphan"
    )
    roles: Mapped[list["Role"]] = relationship(
        "Role", back_populates="team", cascade="all, delete-orphan"
    )


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    team_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=True
    )
    # team_id=NULL means "system role" (the default owner/admin/engineer/reviewer/viewer
    # bundles that every workspace has access to by name).
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Permission strings; validated at write-time against app.iam.permissions.PERMISSIONS.
    permissions: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )

    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    team: Mapped[Team | None] = relationship("Team", back_populates="roles")

    __table_args__ = (
        UniqueConstraint("team_id", "name", name="uq_roles_team_name"),
    )


class TeamMember(Base):
    __tablename__ = "team_members"

    team_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("roles.id", ondelete="SET NULL"), nullable=True
    )
    invited_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    team: Mapped[Team] = relationship("Team", back_populates="members")
    user: Mapped[User] = relationship("User", back_populates="team_memberships", foreign_keys=[user_id])
    role: Mapped[Role | None] = relationship("Role")

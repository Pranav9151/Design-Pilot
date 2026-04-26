"""
KnowledgeChunk — RAG corpus for Triple-Lock "Lock 2" cross-check.

Sources:
- Handbook snippets (Shigley's, Machinery's Handbook) — public, shared.
- Standards excerpts (ISO, ASME) — public, shared.
- User's own past designs — scoped by user_id.
- Team's past designs (v1.5) — scoped by team_id.

Embeddings use pgvector (384-dim via sentence-transformers all-MiniLM-L6-v2
in v1.0; 1536-dim via OpenAI/Anthropic embed-large later if justified).

CRITICAL: RAG results are FILTERED by user_id/team_id BEFORE being
passed to the LLM. No cross-tenant leakage possible (defense in depth:
app filter + DB RLS).
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


# pgvector column mapping: we declare it as a string here and use raw
# SQL in the migration to create the `vector(384)` column type.
# At query time, use raw SQL with `<->` operator for similarity.


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Scope. Exactly one of these three should be non-NULL in practice.
    # (NULL, NULL, NULL) = public/shared (handbook, standards).
    # (user_id, NULL, NULL) = user's private knowledge.
    # (NULL, team_id, NULL) = team-shared knowledge (v1.5).
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    team_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), nullable=True, index=True
    )
    design_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("designs.id", ondelete="CASCADE"), nullable=True, index=True
    )

    # Source provenance
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # source_type: handbook | standard | design | feedback | dfm_rule | formula_note

    source_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    # e.g. "Shigley's 11e, Ch3.4, Eq 3-21" or "design:abc-123"

    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Structured metadata for filtering: {"part_type": "bracket",
    # "material_category": "aluminum", "load_range_n": [100, 1000]}
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)

    # Token count of `content` for chunk-size-aware retrieval.
    tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Embedding column — declared via raw SQL in the migration.
    # SQLAlchemy sees it as a string column for portability.
    # Actual type: `vector(384)` from pgvector extension.
    # Queries use raw SQL: `ORDER BY embedding <-> :query_embedding LIMIT :k`.

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

"""Alembic environment: sync DB URL, imports all models via app.models."""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import Base + all models so `Base.metadata` knows about every table.
from app.core.config import get_settings
from app.core.db import Base
from app.models import (  # noqa: F401  (import-for-side-effect)
    AuditLog,
    CustomMaterial,
    Design,
    DesignDiary,
    DesignFeedback,
    KnowledgeChunk,
    Material,
    Role,
    Team,
    TeamMember,
    User,
)

config = context.config

# Logging
if config.config_file_name:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url with our env-loaded sync URL
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL_SYNC)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to stdout)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode with an engine."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

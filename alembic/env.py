"""Alembic configuration for the NBA ingestion + props tables."""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# ✅ Add the *outer* nba_ingest folder to sys.path:
# E:\VS Code\cautious-funicular\cautious-funicular\nba_ingest
BASE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "nba_ingest")
)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# ✅ Import as a proper package so relative imports like ".models" work
from nba_ingest.config import load_settings
from nba_ingest.models import Base
from nba_ingest.props_models import PropsBase

# Alembic config object
config = context.config

# Logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Load DB URL from your settings and inject into Alembic
settings = load_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)


# Tell Alembic what metadata to use for autogenerate
target_metadata = [Base.metadata, PropsBase.metadata]

# Load DATABASE_URL from your Settings and inject into Alembic config
settings = load_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""

    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

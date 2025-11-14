"""Create hybrid props ingestion tables

Revision ID: 20240501_01
Revises:
Create Date: 2024-05-01
"""
from __future__ import annotations

from datetime import datetime

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20240501_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "players",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("canonical_name", sa.String(length=255), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, default=datetime.utcnow),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, default=datetime.utcnow),
    )

    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_event_id", sa.Text(), nullable=False),
        sa.Column("sport_key", sa.Text()),
        sa.Column("commence_time_utc", sa.DateTime(timezone=True)),
        sa.Column("game_date", sa.Date()),
        sa.Column("home_team", sa.String(length=255)),
        sa.Column("away_team", sa.String(length=255)),
        sa.Column("season", sa.String(length=32)),
        sa.UniqueConstraint("provider", "provider_event_id", name="uq_events_provider_event"),
    )

    op.create_table(
        "props",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id", ondelete="CASCADE"), nullable=False),
        sa.Column("market_key", sa.Text(), nullable=False),
        sa.Column("line", sa.Numeric(10, 3)),
        sa.Column("price", sa.Integer()),
        sa.Column("bookmaker_key", sa.Text()),
        sa.Column("last_update_utc", sa.DateTime(timezone=True)),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("provider_market_key", sa.Text()),
        sa.UniqueConstraint(
            "event_id",
            "player_id",
            "market_key",
            "line",
            "bookmaker_key",
            "provider",
            name="uq_props_event_player_market",
        ),
    )

    op.create_table(
        "checkpoints",
        sa.Column("provider", sa.String(length=16), primary_key=True),
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("provider_event_id", sa.String(length=128), primary_key=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("worker", sa.String(length=32)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.CheckConstraint(
            "status IN ('discovered','processing','done','error')",
            name="ck_checkpoint_status",
        ),
    )

    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("markets", sa.String(length=255), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("events_discovered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("events_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("http_429_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("http_5xx_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("ingestion_runs")
    op.drop_table("checkpoints")
    op.drop_table("props")
    op.drop_table("events")
    op.drop_table("players")

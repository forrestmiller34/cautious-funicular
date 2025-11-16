"""Add ingest job queue, usage tracking, and provider flags.

Revision ID: 2b6f1e6e0c29
Revises: 94c6456d2877
Create Date: 2025-11-14 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "2b6f1e6e0c29"
down_revision: Union[str, Sequence[str], None] = "94c6456d2877"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "games",
        sa.Column("sgo_ingested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "games",
        sa.Column("oddsapi_ingested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "games",
        sa.Column("betsapi_ingested_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "unified_game_odds",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("game_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("market_type", sa.String(length=128), nullable=False),
        sa.Column("bookmaker", sa.String(length=128), nullable=False),
        sa.Column("outcome_key", sa.String(length=128), nullable=False),
        sa.Column("line", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("price", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "game_id",
            "market_type",
            "bookmaker",
            "outcome_key",
            name="uq_unified_odds_identity",
        ),
    )

    op.create_table(
        "odds_ingest_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("game_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", "provider", name="uq_odds_ingest_job"),
    )

    op.create_table(
        "odds_provider_usage",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("snapshot_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("credits_remaining", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("credits_used", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("window_label", sa.String(length=255), nullable=True),
        sa.Column("meta", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("odds_provider_usage")
    op.drop_table("odds_ingest_jobs")
    op.drop_table("unified_game_odds")
    op.drop_column("games", "betsapi_ingested_at")
    op.drop_column("games", "oddsapi_ingested_at")
    op.drop_column("games", "sgo_ingested_at")

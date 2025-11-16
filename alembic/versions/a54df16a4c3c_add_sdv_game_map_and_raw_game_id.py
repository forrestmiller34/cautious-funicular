"""Add SDV game map bridge and raw game_id column.

Revision ID: a54df16a4c3c
Revises: 2b6f1e6e0c29
Create Date: 2025-11-15 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a54df16a4c3c"
down_revision: Union[str, Sequence[str], None] = "2b6f1e6e0c29"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sdv_game_map",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("sdv_game_id", sa.String(length=64), nullable=False),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("game_date", sa.Date(), nullable=False),
        sa.Column("sdv_home_team", sa.String(length=64), nullable=True),
        sa.Column("sdv_away_team", sa.String(length=64), nullable=True),
        sa.Column(
            "internal_game_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            nullable=True,
        ),
        sa.Column(
            "matched", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            server_onupdate=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["internal_game_id"], ["games.id"], ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sdv_game_id", name="uq_sdv_game_map_sdv_game_id"),
    )

    op.add_column(
        "sdv_nba_pbp_2021_raw",
        sa.Column("game_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sdv_nba_pbp_2021_raw", "game_id")
    op.drop_table("sdv_game_map")

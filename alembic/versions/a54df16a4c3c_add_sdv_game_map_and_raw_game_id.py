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
    # Create the sdv_game_map bridge table
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
            "matched",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            server_onupdate=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["internal_game_id"], ["games.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sdv_game_id", name="uq_sdv_game_map_sdv_game_id"),
    )

    # Conditionally add game_id to sdv_nba_pbp_2021_raw
    bind = op.get_bind()
    insp = sa.inspect(bind)

    table_names = insp.get_table_names()
    if "sdv_nba_pbp_2021_raw" in table_names:
        cols = [c["name"] for c in insp.get_columns("sdv_nba_pbp_2021_raw")]
        if "game_id" not in cols:
            op.add_column(
                "sdv_nba_pbp_2021_raw",
                sa.Column("game_id", sa.BigInteger(), nullable=True),
            )
        # else: game_id already exists, don't try to add it again
    # else: raw table doesn't exist yet; we'll add game_id some other way if needed


def downgrade() -> None:
    # Conditionally drop game_id from sdv_nba_pbp_2021_raw
    bind = op.get_bind()
    insp = sa.inspect(bind)

    table_names = insp.get_table_names()
    if "sdv_nba_pbp_2021_raw" in table_names:
        cols = [c["name"] for c in insp.get_columns("sdv_nba_pbp_2021_raw")]
        if "game_id" in cols:
            op.drop_column("sdv_nba_pbp_2021_raw", "game_id")

    # Drop the sdv_game_map table
    op.drop_table("sdv_game_map")
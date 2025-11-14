"""Add season averages, injuries, and raw fields.

Revision ID: 4fb89f8a8f6c
Revises: fad3544c0a82
Create Date: 2024-04-20 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "4fb89f8a8f6c"
down_revision: Union[str, Sequence[str], None] = "fad3544c0a82"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("games", sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("games", sa.Column("status", sa.String(length=64), nullable=True))
    op.add_column("games", sa.Column("period", sa.Integer(), nullable=True))
    op.add_column("games", sa.Column("time", sa.String(length=64), nullable=True))
    op.add_column("games", sa.Column("postseason", sa.Boolean(), nullable=True))
    op.add_column("games", sa.Column("home_q1", sa.Integer(), nullable=True))
    op.add_column("games", sa.Column("home_q2", sa.Integer(), nullable=True))
    op.add_column("games", sa.Column("home_q3", sa.Integer(), nullable=True))
    op.add_column("games", sa.Column("home_q4", sa.Integer(), nullable=True))
    op.add_column("games", sa.Column("home_ot", sa.Integer(), nullable=True))
    op.add_column("games", sa.Column("away_q1", sa.Integer(), nullable=True))
    op.add_column("games", sa.Column("away_q2", sa.Integer(), nullable=True))
    op.add_column("games", sa.Column("away_q3", sa.Integer(), nullable=True))
    op.add_column("games", sa.Column("away_q4", sa.Integer(), nullable=True))
    op.add_column("games", sa.Column("away_ot", sa.Integer(), nullable=True))
    op.add_column("games", sa.Column("home_timeouts_remaining", sa.Integer(), nullable=True))
    op.add_column("games", sa.Column("away_timeouts_remaining", sa.Integer(), nullable=True))
    op.add_column("games", sa.Column("home_in_bonus", sa.Boolean(), nullable=True))
    op.add_column("games", sa.Column("away_in_bonus", sa.Boolean(), nullable=True))

    op.add_column(
        "player_game_advanced",
        sa.Column("pie", sa.Numeric(6, 3), nullable=True),
    )
    op.add_column(
        "player_game_advanced",
        sa.Column("pace", sa.Numeric(6, 2), nullable=True),
    )
    op.add_column(
        "player_game_advanced",
        sa.Column("assist_ratio", sa.Numeric(6, 2), nullable=True),
    )
    op.add_column(
        "player_game_advanced",
        sa.Column("assist_to_turnover", sa.Numeric(6, 2), nullable=True),
    )
    op.add_column(
        "player_game_advanced",
        sa.Column("effective_fg_pct", sa.Numeric(5, 2), nullable=True),
    )
    op.add_column(
        "player_game_advanced",
        sa.Column("net_rating", sa.Numeric(6, 2), nullable=True),
    )
    op.add_column(
        "player_game_advanced",
        sa.Column("rebound_pct", sa.Numeric(5, 2), nullable=True),
    )
    op.add_column(
        "player_game_advanced",
        sa.Column("turnover_ratio", sa.Numeric(6, 2), nullable=True),
    )

    op.create_table(
        "season_averages",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("season_type", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raw_player", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            server_onupdate=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "player_id",
            "season",
            "season_type",
            "category",
            "type",
            name="uq_season_avg_player_season_category",
        ),
    )
    op.create_index(
        "ix_season_averages_player_season",
        "season_averages",
        ["player_id", "season"],
        unique=False,
    )

    op.create_table(
        "injuries",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False),
        sa.Column("league_id", sa.Integer(), nullable=True),
        sa.Column("injury_date", sa.Date(), nullable=False),
        sa.Column("team_name", sa.String(length=255), nullable=False),
        sa.Column("player_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=128), nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("report_time_raw", sa.String(length=32), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=True),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            server_onupdate=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["league_id"], ["leagues.id"], ),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "injury_date",
            "team_name",
            "player_name",
            "status",
            "report_time_raw",
            name="uq_injuries_snapshot",
        ),
    )
    op.create_index("ix_injuries_injury_date", "injuries", ["injury_date"], unique=False)
    op.create_index("ix_injuries_player_date", "injuries", ["player_id", "injury_date"], unique=False)
    op.create_index("ix_injuries_team_date", "injuries", ["team_id", "injury_date"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_injuries_team_date", table_name="injuries")
    op.drop_index("ix_injuries_player_date", table_name="injuries")
    op.drop_index("ix_injuries_injury_date", table_name="injuries")
    op.drop_table("injuries")

    op.drop_index("ix_season_averages_player_season", table_name="season_averages")
    op.drop_table("season_averages")

    op.drop_column("player_game_advanced", "turnover_ratio")
    op.drop_column("player_game_advanced", "rebound_pct")
    op.drop_column("player_game_advanced", "net_rating")
    op.drop_column("player_game_advanced", "effective_fg_pct")
    op.drop_column("player_game_advanced", "assist_to_turnover")
    op.drop_column("player_game_advanced", "assist_ratio")
    op.drop_column("player_game_advanced", "pace")
    op.drop_column("player_game_advanced", "pie")

    op.drop_column("games", "away_in_bonus")
    op.drop_column("games", "home_in_bonus")
    op.drop_column("games", "away_timeouts_remaining")
    op.drop_column("games", "home_timeouts_remaining")
    op.drop_column("games", "away_ot")
    op.drop_column("games", "away_q4")
    op.drop_column("games", "away_q3")
    op.drop_column("games", "away_q2")
    op.drop_column("games", "away_q1")
    op.drop_column("games", "home_ot")
    op.drop_column("games", "home_q4")
    op.drop_column("games", "home_q3")
    op.drop_column("games", "home_q2")
    op.drop_column("games", "home_q1")
    op.drop_column("games", "postseason")
    op.drop_column("games", "time")
    op.drop_column("games", "period")
    op.drop_column("games", "status")
    op.drop_column("games", "raw_json")

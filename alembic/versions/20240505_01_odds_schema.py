"""Add unified odds schema tables and columns."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20240505_01_odds_schema"
down_revision = "20240502_01_unified_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "leagues",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("provider_sport_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint("name", name="uq_leagues_name"),
    )

    op.add_column("teams", sa.Column("league_id", sa.Integer(), nullable=True))
    op.add_column("teams", sa.Column("short_name", sa.String(length=128), nullable=True))
    op.add_column(
        "teams",
        sa.Column(
            "provider_team_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_foreign_key("fk_teams_league_id", "teams", "leagues", ["league_id"], ["id"])

    op.add_column("players", sa.Column("league_id", sa.Integer(), nullable=True))
    op.add_column("players", sa.Column("team_id", sa.Integer(), nullable=True))
    op.add_column(
        "players",
        sa.Column(
            "provider_player_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_foreign_key("fk_players_league_id", "players", "leagues", ["league_id"], ["id"])
    op.create_foreign_key("fk_players_team_id", "players", "teams", ["team_id"], ["id"])

    op.add_column("games", sa.Column("league_id", sa.Integer(), nullable=True))
    op.add_column(
        "games",
        sa.Column(
            "provider_event_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "games",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column(
        "games",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_foreign_key("fk_games_league_id", "games", "leagues", ["league_id"], ["id"])

    op.create_table(
        "odds_books",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "provider_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("name", name="uq_odds_books_name"),
    )

    op.create_table(
        "odds_markets",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.BigInteger(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("market_type", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False, server_default="full_game"),
        sa.Column("participant_type", sa.String(length=16), nullable=False),
        sa.Column("participant_id", sa.Integer(), nullable=True),
        sa.Column("bookmaker_id", sa.Integer(), sa.ForeignKey("odds_books.id"), nullable=False),
        sa.Column("line", sa.Numeric(10, 3), nullable=True),
        sa.Column("price", sa.Integer(), nullable=True),
        sa.Column("side", sa.String(length=32), nullable=True),
        sa.Column("provider_raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_unique_constraint(
        "uq_odds_markets_snapshot",
        "odds_markets",
        [
            "event_id",
            "provider",
            "bookmaker_id",
            "market_type",
            "participant_type",
            "participant_id",
            "side",
            "line",
            "as_of",
        ],
    )

    op.create_table(
        "ingestion_state",
        sa.Column("provider", sa.String(length=64), primary_key=True),
        sa.Column("last_successful_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("ingestion_state")
    op.drop_constraint("uq_odds_markets_snapshot", "odds_markets", type_="unique")
    op.drop_table("odds_markets")
    op.drop_table("odds_books")

    op.drop_constraint("fk_games_league_id", "games", type_="foreignkey")
    op.drop_column("games", "updated_at")
    op.drop_column("games", "created_at")
    op.drop_column("games", "provider_event_ids")
    op.drop_column("games", "league_id")

    op.drop_constraint("fk_players_team_id", "players", type_="foreignkey")
    op.drop_constraint("fk_players_league_id", "players", type_="foreignkey")
    op.drop_column("players", "provider_player_ids")
    op.drop_column("players", "team_id")
    op.drop_column("players", "league_id")

    op.drop_constraint("fk_teams_league_id", "teams", type_="foreignkey")
    op.drop_column("teams", "provider_team_ids")
    op.drop_column("teams", "short_name")
    op.drop_column("teams", "league_id")

    op.drop_table("leagues")

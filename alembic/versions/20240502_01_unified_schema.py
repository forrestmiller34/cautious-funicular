"""Add unified teams/players/games schema"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20240502_01"
down_revision = "20240501_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("players", "name", new_column_name="full_name")
    op.add_column("players", sa.Column("bdl_player_id", sa.Integer(), nullable=True))
    op.add_column("players", sa.Column("nba_player_id", sa.String(length=32), nullable=True))
    op.add_column("players", sa.Column("position", sa.String(length=16), nullable=True))
    op.add_column("players", sa.Column("height", sa.String(length=32), nullable=True))
    op.add_column("players", sa.Column("weight", sa.String(length=32), nullable=True))
    op.create_unique_constraint("uq_players_bdl_player_id", "players", ["bdl_player_id"])
    op.create_unique_constraint("uq_players_nba_player_id", "players", ["nba_player_id"])

    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("abbreviation", sa.String(length=10), nullable=False),
        sa.Column("city", sa.String(length=255), nullable=True),
        sa.Column("conference", sa.String(length=32), nullable=True),
        sa.Column("division", sa.String(length=32), nullable=True),
        sa.Column("bdl_team_id", sa.Integer(), nullable=True),
        sa.Column("nba_team_id", sa.String(length=32), nullable=True),
        sa.Column("canonical_name", sa.String(length=255), nullable=False),
        sa.UniqueConstraint("bdl_team_id", name="uq_teams_bdl_team_id"),
        sa.UniqueConstraint("nba_team_id", name="uq_teams_nba_team_id"),
        sa.UniqueConstraint("canonical_name", name="uq_teams_canonical_name"),
    )

    op.create_table(
        "games",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("game_date", sa.Date(), nullable=False),
        sa.Column("season", sa.String(length=16), nullable=True),
        sa.Column("season_type", sa.String(length=16), nullable=True),
        sa.Column("home_team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("away_team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("home_score", sa.Integer(), nullable=True),
        sa.Column("away_score", sa.Integer(), nullable=True),
        sa.Column("tipoff_datetime_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bdl_game_id", sa.Integer(), nullable=True),
        sa.Column("nba_game_id", sa.String(length=32), nullable=True),
        sa.Column("odds_api_event_id", sa.String(length=64), nullable=True),
        sa.Column("sgo_event_id", sa.String(length=64), nullable=True),
        sa.UniqueConstraint("bdl_game_id", name="uq_games_bdl_game_id"),
        sa.UniqueConstraint("nba_game_id", name="uq_games_nba_game_id"),
        sa.UniqueConstraint("odds_api_event_id", name="uq_games_odds_event_id"),
        sa.UniqueConstraint("sgo_event_id", name="uq_games_sgo_event_id"),
    )
    op.create_index(
        "ix_games_date_home_away",
        "games",
        ["game_date", "home_team_id", "away_team_id"],
        unique=False,
    )

    op.create_table(
        "player_game_stats",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("game_id", sa.BigInteger(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("minutes", sa.String(length=16), nullable=True),
        sa.Column("points", sa.Integer(), nullable=True),
        sa.Column("rebounds", sa.Integer(), nullable=True),
        sa.Column("assists", sa.Integer(), nullable=True),
        sa.Column("blocks", sa.Integer(), nullable=True),
        sa.Column("steals", sa.Integer(), nullable=True),
        sa.Column("fg_attempts", sa.Integer(), nullable=True),
        sa.Column("fg_made", sa.Integer(), nullable=True),
        sa.Column("three_attempts", sa.Integer(), nullable=True),
        sa.Column("three_made", sa.Integer(), nullable=True),
        sa.Column("ft_attempts", sa.Integer(), nullable=True),
        sa.Column("ft_made", sa.Integer(), nullable=True),
        sa.Column("turnovers", sa.Integer(), nullable=True),
        sa.Column("plus_minus", sa.Integer(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.UniqueConstraint("game_id", "player_id", name="uq_player_game_stats_game_player"),
    )

    op.create_table(
        "player_game_advanced",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("game_id", sa.BigInteger(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("minutes", sa.Numeric(6, 2), nullable=True),
        sa.Column("off_rating", sa.Numeric(6, 2), nullable=True),
        sa.Column("def_rating", sa.Numeric(6, 2), nullable=True),
        sa.Column("usage_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("ts_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("offensive_reb_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("defensive_reb_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("assist_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("steal_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("block_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.UniqueConstraint("game_id", "player_id", name="uq_player_game_adv_game_player"),
    )

    op.create_table(
        "play_by_play",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("game_id", sa.BigInteger(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("event_num", sa.Integer(), nullable=False),
        sa.Column("period", sa.Integer(), nullable=True),
        sa.Column("clock", sa.String(length=16), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=True),
        sa.Column("player1_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=True),
        sa.Column("player2_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=True),
        sa.Column("player3_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=True),
        sa.Column("home_score", sa.Integer(), nullable=True),
        sa.Column("away_score", sa.Integer(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.UniqueConstraint("game_id", "event_num", name="uq_pbp_game_event"),
    )

    op.create_table(
        "game_odds",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("game_id", sa.BigInteger(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("bookmaker_key", sa.String(length=128), nullable=False),
        sa.Column("market_key", sa.String(length=128), nullable=False),
        sa.Column("line_type", sa.String(length=32), nullable=False),
        sa.Column("participant_type", sa.String(length=16), nullable=False),
        sa.Column("participant_name", sa.String(length=255), nullable=False),
        sa.Column("participant_team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=True),
        sa.Column("participant_player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=True),
        sa.Column("side", sa.String(length=32), nullable=True),
        sa.Column("line", sa.Numeric(10, 3), nullable=True),
        sa.Column("price", sa.Integer(), nullable=True),
        sa.Column("last_update_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "snapshot_ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.UniqueConstraint(
            "game_id",
            "provider",
            "bookmaker_key",
            "market_key",
            "participant_type",
            "participant_name",
            "side",
            "line",
            "snapshot_ts",
            name="uq_game_odds_snapshot",
        ),
        sa.CheckConstraint(
            "(participant_type = 'team' AND participant_team_id IS NOT NULL AND participant_player_id IS NULL) "
            "OR (participant_type = 'player' AND participant_player_id IS NOT NULL AND participant_team_id IS NULL) "
            "OR (participant_type NOT IN ('team','player') AND participant_team_id IS NULL AND participant_player_id IS NULL)",
            name="ck_game_odds_participant",
        ),
    )


def downgrade() -> None:
    op.drop_table("game_odds")
    op.drop_table("play_by_play")
    op.drop_table("player_game_advanced")
    op.drop_table("player_game_stats")
    op.drop_index("ix_games_date_home_away", table_name="games")
    op.drop_table("games")
    op.drop_table("teams")
    op.drop_constraint("uq_players_nba_player_id", "players", type_="unique")
    op.drop_constraint("uq_players_bdl_player_id", "players", type_="unique")
    op.drop_column("players", "weight")
    op.drop_column("players", "height")
    op.drop_column("players", "position")
    op.drop_column("players", "nba_player_id")
    op.drop_column("players", "bdl_player_id")
    op.alter_column("players", "full_name", new_column_name="name")

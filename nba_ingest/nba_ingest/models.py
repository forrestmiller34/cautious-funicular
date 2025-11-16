"""Unified SQLAlchemy ORM models for the NBA ingestion warehouse."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, synonym


class Base(DeclarativeBase):
    """Declarative base shared by all warehouse tables."""


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # pragma: no cover - dialect shim
    return "JSON"


BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")


class League(Base):
    """Top-level leagues such as the NBA."""

    __tablename__ = "leagues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    provider_sport_keys: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    teams: Mapped[list["Team"]] = relationship("Team", back_populates="league")
    players: Mapped[list["Player"]] = relationship("Player", back_populates="league")
    events: Mapped[list["Game"]] = relationship("Game", back_populates="league")


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_id: Mapped[int | None] = mapped_column(ForeignKey("leagues.id"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    short_name: Mapped[str | None] = mapped_column(String(128))
    abbrev: Mapped[str] = mapped_column("abbreviation", String(10), nullable=False)
    city: Mapped[str | None] = mapped_column(String(255))
    conference: Mapped[str | None] = mapped_column(String(32))
    division: Mapped[str | None] = mapped_column(String(32))
    bdl_team_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    nba_team_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    canonical_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    provider_team_ids: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    abbreviation = synonym("abbrev")

    league: Mapped[League | None] = relationship("League", back_populates="teams")
    home_games: Mapped[list[Game]] = relationship(
        "Game", back_populates="home_team", foreign_keys="Game.home_team_id"
    )
    away_games: Mapped[list[Game]] = relationship(
        "Game", back_populates="away_team", foreign_keys="Game.away_team_id"
    )

    __table_args__ = (
        UniqueConstraint("bdl_team_id", name="uq_teams_bdl_team_id"),
        UniqueConstraint("nba_team_id", name="uq_teams_nba_team_id"),
        UniqueConstraint("canonical_name", name="uq_teams_canonical_name"),
    )


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_id: Mapped[int | None] = mapped_column(ForeignKey("leagues.id"))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    bdl_player_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    nba_player_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    position: Mapped[str | None] = mapped_column(String(16))
    height: Mapped[str | None] = mapped_column(String(32))
    weight: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    provider_player_ids: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    league: Mapped[League | None] = relationship("League", back_populates="players")
    team: Mapped[Team | None] = relationship("Team")
    __table_args__ = (
        UniqueConstraint("bdl_player_id", name="uq_players_bdl_player_id"),
        UniqueConstraint("nba_player_id", name="uq_players_nba_player_id"),
    )


class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    league_id: Mapped[int | None] = mapped_column(ForeignKey("leagues.id"))
    game_date: Mapped[Date] = mapped_column(Date, nullable=False)
    season: Mapped[str | None] = mapped_column(String(16))
    season_type: Mapped[str | None] = mapped_column(String(16))
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    start_time_utc: Mapped[datetime | None] = mapped_column(
        "tipoff_datetime_utc", DateTime(timezone=True)
    )
    bdl_game_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    nba_game_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    odds_api_event_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    sgo_event_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    provider_event_ids: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    raw_json: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str | None] = mapped_column(String(64))
    period: Mapped[int | None] = mapped_column(Integer)
    time: Mapped[str | None] = mapped_column(String(64))
    postseason: Mapped[bool | None] = mapped_column(Boolean)
    sgo_ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    oddsapi_ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    betsapi_ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    home_q1: Mapped[int | None] = mapped_column(Integer)
    home_q2: Mapped[int | None] = mapped_column(Integer)
    home_q3: Mapped[int | None] = mapped_column(Integer)
    home_q4: Mapped[int | None] = mapped_column(Integer)
    home_ot: Mapped[int | None] = mapped_column(Integer)
    away_q1: Mapped[int | None] = mapped_column(Integer)
    away_q2: Mapped[int | None] = mapped_column(Integer)
    away_q3: Mapped[int | None] = mapped_column(Integer)
    away_q4: Mapped[int | None] = mapped_column(Integer)
    away_ot: Mapped[int | None] = mapped_column(Integer)
    home_timeouts_remaining: Mapped[int | None] = mapped_column(Integer)
    away_timeouts_remaining: Mapped[int | None] = mapped_column(Integer)
    home_in_bonus: Mapped[bool | None] = mapped_column(Boolean)
    away_in_bonus: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    tipoff_datetime_utc = synonym("start_time_utc")
    league: Mapped[League | None] = relationship("League", back_populates="events")
    home_team: Mapped[Team] = relationship(
        Team, foreign_keys=[home_team_id], back_populates="home_games"
    )
    away_team: Mapped[Team] = relationship(
        Team, foreign_keys=[away_team_id], back_populates="away_games"
    )
    player_stats: Mapped[list[PlayerGameStat]] = relationship(
        "PlayerGameStat", back_populates="game"
    )
    advanced_stats: Mapped[list[PlayerGameAdvanced]] = relationship(
        "PlayerGameAdvanced", back_populates="game"
    )
    play_by_play_events: Mapped[list[PlayByPlayEvent]] = relationship(
        "PlayByPlayEvent", back_populates="game"
    )
    odds: Mapped[list[GameOdds]] = relationship("GameOdds", back_populates="game")
    ingest_jobs: Mapped[list["OddsIngestJob"]] = relationship(
        "OddsIngestJob", back_populates="game"
    )
    unified_odds: Mapped[list["UnifiedGameOdds"]] = relationship(
        "UnifiedGameOdds", back_populates="game"
    )

    __table_args__ = (
        UniqueConstraint("bdl_game_id", name="uq_games_bdl_game_id"),
        UniqueConstraint("nba_game_id", name="uq_games_nba_game_id"),
        UniqueConstraint("odds_api_event_id", name="uq_games_odds_event_id"),
        UniqueConstraint("sgo_event_id", name="uq_games_sgo_event_id"),
        Index("ix_games_date_home_away", "game_date", "home_team_id", "away_team_id"),
    )


class PlayerGameStat(Base):
    __tablename__ = "player_game_stats"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    minutes: Mapped[str | None] = mapped_column(String(16))
    points: Mapped[int | None] = mapped_column(Integer)
    rebounds: Mapped[int | None] = mapped_column(Integer)
    assists: Mapped[int | None] = mapped_column(Integer)
    blocks: Mapped[int | None] = mapped_column(Integer)
    steals: Mapped[int | None] = mapped_column(Integer)
    fg_attempts: Mapped[int | None] = mapped_column(Integer)
    fg_made: Mapped[int | None] = mapped_column(Integer)
    three_attempts: Mapped[int | None] = mapped_column(Integer)
    three_made: Mapped[int | None] = mapped_column(Integer)
    ft_attempts: Mapped[int | None] = mapped_column(Integer)
    ft_made: Mapped[int | None] = mapped_column(Integer)
    turnovers: Mapped[int | None] = mapped_column(Integer)
    plus_minus: Mapped[int | None] = mapped_column(Integer)
    raw_json: Mapped[dict | None] = mapped_column(JSONB)

    game: Mapped[Game] = relationship("Game", back_populates="player_stats")
    player: Mapped[Player] = relationship("Player")
    team: Mapped[Team] = relationship("Team")

    __table_args__ = (
        UniqueConstraint("game_id", "player_id", name="uq_player_game_stats_game_player"),
    )


class PlayerGameAdvanced(Base):
    __tablename__ = "player_game_advanced"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    minutes: Mapped[float | None] = mapped_column(Numeric(6, 2))
    off_rating: Mapped[float | None] = mapped_column(Numeric(6, 2))
    def_rating: Mapped[float | None] = mapped_column(Numeric(6, 2))
    usage_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    ts_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    offensive_reb_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    defensive_reb_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    assist_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    steal_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    block_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    pie: Mapped[float | None] = mapped_column(Numeric(6, 3))
    pace: Mapped[float | None] = mapped_column(Numeric(6, 2))
    assist_ratio: Mapped[float | None] = mapped_column(Numeric(6, 2))
    assist_to_turnover: Mapped[float | None] = mapped_column(Numeric(6, 2))
    effective_fg_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    net_rating: Mapped[float | None] = mapped_column(Numeric(6, 2))
    rebound_pct: Mapped[float | None] = mapped_column(Numeric(5, 2))
    turnover_ratio: Mapped[float | None] = mapped_column(Numeric(6, 2))
    raw_json: Mapped[dict | None] = mapped_column(JSONB)

    game: Mapped[Game] = relationship("Game", back_populates="advanced_stats")
    player: Mapped[Player] = relationship("Player")
    team: Mapped[Team] = relationship("Team")

    __table_args__ = (
        UniqueConstraint("game_id", "player_id", name="uq_player_game_adv_game_player"),
    )


class PlayByPlayEvent(Base):
    __tablename__ = "play_by_play"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    event_num: Mapped[int] = mapped_column(Integer, nullable=False)
    period: Mapped[int | None] = mapped_column(Integer)
    clock: Mapped[str | None] = mapped_column(String(16))
    event_type: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    player1_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    player2_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    player3_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    raw_json: Mapped[dict | None] = mapped_column(JSONB)

    game: Mapped[Game] = relationship("Game", back_populates="play_by_play_events")

    __table_args__ = (
        UniqueConstraint("game_id", "event_num", name="uq_pbp_game_event"),
    )


class SeasonAverage(Base):
    __tablename__ = "season_averages"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    season_type: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    stat_type: Mapped[str] = mapped_column("type", String(32), nullable=False)
    stats: Mapped[dict] = mapped_column(JSONB, nullable=False)
    raw_player: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    player: Mapped[Player] = relationship("Player")

    __table_args__ = (
        UniqueConstraint(
            "player_id",
            "season",
            "season_type",
            "category",
            "type",
            name="uq_season_avg_player_season_category",
        ),
        Index("ix_season_averages_player_season", "player_id", "season"),
    )


class GameOdds(Base):
    __tablename__ = "game_odds"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    bookmaker_key: Mapped[str] = mapped_column(String(128), nullable=False)
    market_key: Mapped[str] = mapped_column(String(128), nullable=False)
    line_type: Mapped[str] = mapped_column(String(32), nullable=False)
    participant_type: Mapped[str] = mapped_column(String(16), nullable=False)
    participant_name: Mapped[str] = mapped_column(String(255), nullable=False)
    participant_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    participant_player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    side: Mapped[str | None] = mapped_column(String(32))
    line: Mapped[float | None] = mapped_column(Numeric(10, 3))
    price: Mapped[int | None] = mapped_column(Integer)
    last_update_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    extra: Mapped[dict | None] = mapped_column(JSONB)

    game: Mapped[Game] = relationship("Game", back_populates="odds")

    __table_args__ = (
        UniqueConstraint(
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
        CheckConstraint(
            "(participant_type = 'team' AND participant_team_id IS NOT NULL AND participant_player_id IS NULL) "
            "OR (participant_type = 'player' AND participant_player_id IS NOT NULL AND participant_team_id IS NULL) "
            "OR (participant_type NOT IN ('team','player') AND participant_team_id IS NULL AND participant_player_id IS NULL)",
            name="ck_game_odds_participant",
        ),
    )


class UnifiedGameOdds(Base):
    """Idempotent odds table keyed by provider, market, and bookmaker."""

    __tablename__ = "unified_game_odds"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    market_type: Mapped[str] = mapped_column(String(128), nullable=False)
    bookmaker: Mapped[str] = mapped_column(String(128), nullable=False)
    outcome_key: Mapped[str] = mapped_column(String(128), nullable=False)
    line: Mapped[float | None] = mapped_column(Numeric(12, 3))
    price: Mapped[float | None] = mapped_column(Numeric(12, 3))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    extra: Mapped[dict | None] = mapped_column(JSONB)

    game: Mapped[Game] = relationship("Game", back_populates="unified_odds")

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "game_id",
            "market_type",
            "bookmaker",
            "outcome_key",
            name="uq_unified_odds_identity",
        ),
    )


class OddsBook(Base):
    __tablename__ = "odds_books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    provider_ids: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    markets: Mapped[list["OddsMarket"]] = relationship("OddsMarket", back_populates="bookmaker")


class OddsMarket(Base):
    __tablename__ = "odds_markets"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    market_type: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="full_game")
    participant_type: Mapped[str] = mapped_column(String(16), nullable=False)
    participant_id: Mapped[int | None] = mapped_column(Integer)
    bookmaker_id: Mapped[int] = mapped_column(ForeignKey("odds_books.id"), nullable=False)
    line: Mapped[float | None] = mapped_column(Numeric(10, 3))
    price: Mapped[int | None] = mapped_column(Integer)
    side: Mapped[str | None] = mapped_column(String(32))
    provider_raw: Mapped[dict | None] = mapped_column(JSONB)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    identity_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    event: Mapped[Game] = relationship("Game")
    bookmaker: Mapped[OddsBook] = relationship("OddsBook", back_populates="markets")

    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "provider",
            "bookmaker_id",
            "market_type",
            "participant_type",
            "participant_id",
            "side",
            "line",
            "as_of",
            name="uq_odds_markets_snapshot",
        ),
        UniqueConstraint("identity_key", name="uq_odds_markets_identity"),
    )


class OddsIngestJob(Base):
    """Tracks provider/game ingest jobs for concurrent workers."""

    __tablename__ = "odds_ingest_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    game: Mapped[Game] = relationship("Game", back_populates="ingest_jobs")

    __table_args__ = (
        UniqueConstraint("game_id", "provider", name="uq_odds_ingest_job"),
    )


class OddsProviderUsage(Base):
    """Snapshots of provider credit usage."""

    __tablename__ = "odds_provider_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    credits_remaining: Mapped[float | None] = mapped_column(Numeric(14, 2))
    credits_used: Mapped[float | None] = mapped_column(Numeric(14, 2))
    window_label: Mapped[str | None] = mapped_column(String(255))
    meta: Mapped[str | None] = mapped_column(Text)


class IngestionState(Base):
    __tablename__ = "ingestion_state"

    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_successful_date: Mapped[Date | None] = mapped_column(Date)


class Injury(Base):
    __tablename__ = "injuries"

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    league_id: Mapped[int | None] = mapped_column(ForeignKey("leagues.id"))
    injury_date: Mapped[Date] = mapped_column(Date, nullable=False)
    team_name: Mapped[str] = mapped_column(String(255), nullable=False)
    player_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str | None] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(String(255))
    report_time_raw: Mapped[str | None] = mapped_column(String(32))
    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    league: Mapped[League | None] = relationship("League")
    player: Mapped[Player | None] = relationship("Player")
    team: Mapped[Team | None] = relationship("Team")

    __table_args__ = (
        UniqueConstraint(
            "injury_date",
            "team_name",
            "player_name",
            "status",
            "report_time_raw",
            name="uq_injuries_snapshot",
        ),
        Index("ix_injuries_injury_date", "injury_date"),
        Index("ix_injuries_player_date", "player_id", "injury_date"),
        Index("ix_injuries_team_date", "team_id", "injury_date"),
    )


class IngestionStatus(Base):
    __tablename__ = "ingestion_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    data_type: Mapped[str] = mapped_column(String(50), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "source",
            "season",
            "data_type",
            name="uq_ingestion_status_source_season_type",
        ),
    )


__all__ = [
    "Base",
    "League",
    "Team",
    "Player",
    "Game",
    "PlayerGameStat",
    "PlayerGameAdvanced",
    "SeasonAverage",
    "PlayByPlayEvent",
    "GameOdds",
    "UnifiedGameOdds",
    "OddsBook",
    "OddsMarket",
    "OddsIngestJob",
    "OddsProviderUsage",
    "IngestionState",
    "IngestionStatus",
    "Injury",
]

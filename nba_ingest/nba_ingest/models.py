"""SQLAlchemy models for NBA data sourced from Ball Don't Lie."""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    BigInteger,
)
from sqlalchemy.orm import declarative_base, relationship


Base = declarative_base()


class NBATeam(Base):
    __tablename__ = "nba_teams"

    id = Column(Integer, primary_key=True)
    abbreviation = Column(String(10), nullable=False)
    full_name = Column(String(100), nullable=False)
    city = Column(String(100))
    conference = Column(String(20))
    division = Column(String(50))

    home_games = relationship(
        "NBAGame",
        back_populates="home_team",
        foreign_keys="NBAGame.home_team_id",
        cascade="all, delete-orphan",
    )
    visitor_games = relationship(
        "NBAGame",
        back_populates="visitor_team",
        foreign_keys="NBAGame.visitor_team_id",
        cascade="all, delete-orphan",
    )


class NBAGame(Base):
    __tablename__ = "nba_games"

    id = Column(BigInteger, primary_key=True)
    season = Column(Integer, nullable=False)
    date = Column(Date, nullable=False)
    datetime = Column(DateTime(timezone=True))
    status = Column(String(50), nullable=False)
    period = Column(Integer, nullable=False)
    time = Column(String(50))
    postseason = Column(Boolean, nullable=False)
    home_team_id = Column(Integer, ForeignKey("nba_teams.id"), nullable=False)
    visitor_team_id = Column(Integer, ForeignKey("nba_teams.id"), nullable=False)
    home_team_score = Column(Integer, nullable=False)
    visitor_team_score = Column(Integer, nullable=False)
    home_q1 = Column(Integer)
    home_q2 = Column(Integer)
    home_q3 = Column(Integer)
    home_q4 = Column(Integer)
    home_ot1 = Column(Integer)
    home_ot2 = Column(Integer)
    home_ot3 = Column(Integer)
    home_timeouts_remaining = Column(Integer)
    home_in_bonus = Column(Boolean)
    visitor_q1 = Column(Integer)
    visitor_q2 = Column(Integer)
    visitor_q3 = Column(Integer)
    visitor_q4 = Column(Integer)
    visitor_ot1 = Column(Integer)
    visitor_ot2 = Column(Integer)
    visitor_ot3 = Column(Integer)
    visitor_timeouts_remaining = Column(Integer)
    visitor_in_bonus = Column(Boolean)

    home_team = relationship("NBATeam", foreign_keys=[home_team_id], back_populates="home_games")
    visitor_team = relationship("NBATeam", foreign_keys=[visitor_team_id], back_populates="visitor_games")
    advanced_stats = relationship(
        "NBAPlayerAdvancedStats",
        back_populates="game",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_nba_games_season_date", "season", "date"),
        Index("ix_nba_games_home_visitor", "home_team_id", "visitor_team_id"),
    )


class NBAPlayerAdvancedStats(Base):
    __tablename__ = "nba_player_advanced_stats"

    id = Column(BigInteger, primary_key=True)
    game_id = Column(BigInteger, ForeignKey("nba_games.id"), nullable=False)
    team_id = Column(Integer, ForeignKey("nba_teams.id"), nullable=False)
    player_id = Column(BigInteger, nullable=False)
    season = Column(Integer, nullable=False)
    postseason = Column(Boolean, nullable=False)
    pie = Column(Float)
    pace = Column(Float)
    assist_percentage = Column(Float)
    assist_ratio = Column(Float)
    assist_to_turnover = Column(Float)
    defensive_rating = Column(Float)
    defensive_rebound_percentage = Column(Float)
    effective_field_goal_percentage = Column(Float)
    net_rating = Column(Float)
    offensive_rating = Column(Float)
    offensive_rebound_percentage = Column(Float)
    rebound_percentage = Column(Float)
    true_shooting_percentage = Column(Float)
    turnover_ratio = Column(Float)
    usage_percentage = Column(Float)

    game = relationship("NBAGame", back_populates="advanced_stats")
    team = relationship("NBATeam")

    __table_args__ = (
        Index("ix_nba_player_adv_stats_season_team", "season", "team_id"),
        Index("ix_nba_player_adv_stats_game_team", "game_id", "team_id"),
    )


__all__ = ["Base", "NBATeam", "NBAGame", "NBAPlayerAdvancedStats"]

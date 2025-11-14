"""ORM models for the hybrid props ingestion pipeline."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .models import Player


class PropsBase(DeclarativeBase):
    """Declarative base for props specific tables."""


class Event(PropsBase):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_event_id: Mapped[str] = mapped_column(Text, nullable=False)
    sport_key: Mapped[str | None] = mapped_column(Text)
    commence_time_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    game_date: Mapped[Date | None] = mapped_column(Date)
    home_team: Mapped[str | None] = mapped_column(String(255))
    away_team: Mapped[str | None] = mapped_column(String(255))
    season: Mapped[str | None] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id", name="uq_events_provider_event"),
    )

    props: Mapped[list[Prop]] = relationship("Prop", back_populates="event")


class Prop(PropsBase):
    __tablename__ = "props"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    market_key: Mapped[str] = mapped_column(Text, nullable=False)
    line: Mapped[Numeric | None] = mapped_column(Numeric(10, 3))
    price: Mapped[int | None] = mapped_column(Integer)
    bookmaker_key: Mapped[str | None] = mapped_column(Text)
    last_update_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_market_key: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "player_id",
            "market_key",
            "line",
            "bookmaker_key",
            "provider",
            name="uq_props_event_player_market",
        ),
    )

    event: Mapped[Event] = relationship("Event", back_populates="props")
    player = relationship(Player)


class Checkpoint(PropsBase):
    __tablename__ = "checkpoints"

    provider: Mapped[str] = mapped_column(String(16), primary_key=True)
    date: Mapped[Date] = mapped_column(Date, primary_key=True)
    provider_event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    worker: Mapped[str | None] = mapped_column(String(32))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "status IN ('discovered','processing','done','error')",
            name="ck_checkpoint_status",
        ),
    )


class IngestionRun(PropsBase):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    markets: Mapped[str] = mapped_column(String(255), nullable=False)
    params: Mapped[dict | None] = mapped_column(JSONB)
    events_discovered: Mapped[int] = mapped_column(Integer, default=0)
    events_processed: Mapped[int] = mapped_column(Integer, default=0)
    http_429_count: Mapped[int] = mapped_column(Integer, default=0)
    http_5xx_count: Mapped[int] = mapped_column(Integer, default=0)


__all__ = [
    "PropsBase",
    "Event",
    "Prop",
    "Checkpoint",
    "IngestionRun",
]

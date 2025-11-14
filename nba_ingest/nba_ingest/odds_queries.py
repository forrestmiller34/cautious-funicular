"""Read-only helper queries for inspecting ingested odds."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Game, OddsMarket


@dataclass(frozen=True)
class EventCount:
    event_date: date
    total_events: int


def count_events_by_date(
    session: Session, start_date: date, end_date: date
) -> list[EventCount]:
    """Return the number of events per date within the supplied range."""
    event_date = func.coalesce(Game.game_date, func.date(Game.start_time_utc)).label(
        "event_date"
    )
    stmt = (
        select(event_date, func.count())
        .where(event_date >= start_date)
        .where(event_date <= end_date)
        .group_by(event_date)
        .order_by(event_date)
    )
    return [EventCount(event_date=row[0], total_events=row[1]) for row in session.execute(stmt)]


@dataclass(frozen=True)
class OddsBreakdown:
    provider: str
    market_type: str
    participant_type: str
    total_rows: int


def list_event_odds_breakdown(session: Session, event_id: int) -> list[OddsBreakdown]:
    """List odds grouped by provider/market/participant type for a single event."""
    stmt = (
        select(
            OddsMarket.provider,
            OddsMarket.market_type,
            OddsMarket.participant_type,
            func.count().label("total_rows"),
        )
        .where(OddsMarket.event_id == event_id)
        .group_by(OddsMarket.provider, OddsMarket.market_type, OddsMarket.participant_type)
        .order_by(
            OddsMarket.provider,
            OddsMarket.market_type,
            OddsMarket.participant_type,
        )
    )
    return [
        OddsBreakdown(
            provider=row[0],
            market_type=row[1],
            participant_type=row[2],
            total_rows=row[3],
        )
        for row in session.execute(stmt)
    ]


__all__ = ["count_events_by_date", "EventCount", "list_event_odds_breakdown", "OddsBreakdown"]

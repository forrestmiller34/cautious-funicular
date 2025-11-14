"""Ingest SportsGameOdds props into the unified game_odds table."""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta, date
from typing import Iterable, Sequence

from dotenv import load_dotenv
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .db import create_db_engine, create_session_factory, get_session
from .models import Base, Game, GameOdds, Player, Team
from .normalization import canonicalize_player_name, canonicalize_team_name
from .sports_game_odds_client import SportsGameOddsClient


def log(message: str) -> None:
    print(f"[SGO] {message}", flush=True)


@dataclass(slots=True)
class SgoSettings:
    api_key: str
    base_url: str
    database_url: str
    markets: list[str]
    region: str
    include_alt_lines: bool


def _load_settings(markets_override: list[str] | None) -> SgoSettings:
    load_dotenv()
    api_key = os.environ.get("SGO_API_KEY")
    if not api_key:
        raise RuntimeError("SGO_API_KEY is required")
    base_url = os.environ.get("SGO_BASE_URL", "https://api.sportsgameodds.com")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    markets_env = markets_override or [m.strip() for m in os.environ.get("PROP_MARKETS", "player_points").split(",") if m.strip()]
    region = os.environ.get("REGION", "us")
    include_alt = os.environ.get("SGO_INCLUDE_ALT_LINES", "false").lower() in {"1", "true", "yes"}
    return SgoSettings(
        api_key=api_key,
        base_url=base_url,
        database_url=database_url,
        markets=markets_env,
        region=region,
        include_alt_lines=include_alt,
    )


def _daterange(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _match_team(session: Session, name: str | None) -> Team | None:
    if not name:
        return None
    canonical = canonicalize_team_name(name)
    return session.execute(select(Team).where(Team.canonical_name == canonical)).scalar_one_or_none()


def _match_game(session: Session, home: Team | None, away: Team | None, game_date: date) -> Game | None:
    if not (home and away):
        return None
    return session.execute(
        select(Game).where(
            Game.game_date == game_date,
            Game.home_team_id == home.id,
            Game.away_team_id == away.id,
        )
    ).scalar_one_or_none()


def _ensure_player(session: Session, name: str | None) -> Player | None:
    if not name:
        return None
    canonical = canonicalize_player_name(name)
    stmt = (
        insert(Player)
        .values(full_name=name, canonical_name=canonical)
        .on_conflict_do_update(index_elements=[Player.canonical_name], set_={"full_name": name})
        .returning(Player)
    )
    return session.execute(stmt).scalar_one()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def _upsert(session: Session, payload: dict) -> None:
    stmt = (
        insert(GameOdds)
        .values(**payload)
        .on_conflict_do_update(
            constraint="uq_game_odds_snapshot",
            set_={
                "price": payload.get("price"),
                "line": payload.get("line"),
                "last_update_utc": payload.get("last_update_utc"),
                "extra": payload.get("extra"),
            },
        )
    )
    session.execute(stmt)


def ingest_date(
    session: Session,
    client: SportsGameOddsClient,
    target_date: date,
    settings: SgoSettings,
) -> int:
    events = client.list_events_by_date(target_date)
    log(f"SportsGameOdds: {target_date}: fetched {len(events)} events from provider.")
    if not events:
        log(f"SportsGameOdds: {target_date}: no events available, skipping.")
        return 0
    rows = 0
    for event in events:
        home_team = _match_team(session, event.get("home_team") or event.get("homeTeam"))
        away_team = _match_team(session, event.get("away_team") or event.get("awayTeam"))
        commence = _parse_iso(event.get("start_time") or event.get("startTime"))
        game = _match_game(session, home_team, away_team, (commence.date() if commence else target_date))
        if not game:
            continue
        event_id = str(event.get("event_id") or event.get("eventId"))
        if event_id and not game.sgo_event_id:
            session.execute(update(Game).where(Game.id == game.id).values(sgo_event_id=event_id))
        if not event_id:
            continue
        bookmakers = client.get_event_props(
            event_id,
            settings.markets,
            settings.region,
            include_alt_lines=settings.include_alt_lines,
        )
        for bookmaker in bookmakers:
            book_key = bookmaker.get("key") or bookmaker.get("title") or "sgo"
            for market in bookmaker.get("markets", []):
                market_key = market.get("key") or market.get("market")
                for outcome in market.get("outcomes", []):
                    player = _ensure_player(session, outcome.get("name"))
                    if not player:
                        continue
                    price = outcome.get("price") or outcome.get("odds")
                    point = outcome.get("point") or outcome.get("line")
                    try:
                        line_value = float(point) if point is not None else None
                    except (TypeError, ValueError):
                        line_value = None
                    payload = {
                        "game_id": game.id,
                        "provider": "sgo",
                        "bookmaker_key": book_key,
                        "market_key": market_key,
                        "line_type": "prop",
                        "participant_type": "player",
                        "participant_name": player.full_name,
                        "participant_team_id": None,
                        "participant_player_id": player.id,
                        "side": outcome.get("type") or outcome.get("description"),
                        "line": line_value,
                        "price": price,
                        "last_update_utc": _parse_iso(
                            outcome.get("last_update")
                            or market.get("last_update")
                            or bookmaker.get("last_update")
                        ),
                        "snapshot_ts": datetime.now(timezone.utc),
                        "extra": {
                            "provider_market_key": market_key,
                            "bookmaker": bookmaker.get("title"),
                        },
                    }
                    _upsert(session, payload)
                    rows += 1
    return rows


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ingest SportsGameOdds props into game_odds")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--markets", help="Comma separated list of markets")
    args = parser.parse_args(argv)

    markets_override = None
    if args.markets:
        markets_override = [m.strip() for m in args.markets.split(",") if m.strip()]

    settings = _load_settings(markets_override)
    start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end, "%Y-%m-%d").date()
    log(
        f"Starting SportsGameOdds ingest from {start_date} to {end_date}, "
        f"sports=['nba'] markets={settings.markets}."
    )

    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    client = SportsGameOddsClient(settings.api_key, settings.base_url)

    with get_session(session_factory) as session:
        total_rows = 0
        for target_date in _daterange(start_date, end_date):
            log(f"nba: processing date {target_date}...")
            before_rows = total_rows
            total_rows += ingest_date(session, client, target_date, settings)
            delta = total_rows - before_rows
            if delta == 0:
                log(f"nba: {target_date}: no props rows upserted.")
            else:
                log(f"nba: {target_date}: upserted {delta} props rows into game_odds.")
        log(f"Finished SportsGameOdds ingest. Inserted/updated {total_rows} rows.")


if __name__ == "__main__":
    main()

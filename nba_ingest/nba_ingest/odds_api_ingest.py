"""Ingest team odds and player props from The Odds API into the unified schema."""
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
from .odds_api_client import TheOddsApiClient


@dataclass(slots=True)
class OddsSettings:
    api_key: str
    base_url: str
    database_url: str
    region: str
    markets: list[str]
    snapshot_time: str


def _load_settings(markets_override: list[str] | None) -> OddsSettings:
    load_dotenv()
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        raise RuntimeError("ODDS_API_KEY is required")
    base_url = os.environ.get("ODDS_API_BASE_URL", "https://api.the-odds-api.com/v4")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    region = os.environ.get("REGION", "us")
    markets_env = markets_override or [m.strip() for m in os.environ.get("PROP_MARKETS", "player_points").split(",") if m.strip()]
    snapshot_time = os.environ.get("HISTORICAL_SNAPSHOT", "12:00:00Z")
    return OddsSettings(
        api_key=api_key,
        base_url=base_url,
        database_url=database_url,
        region=region,
        markets=markets_env,
        snapshot_time=snapshot_time,
    )


def _daterange(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _snapshot_iso(target_date: date, snapshot_time: str) -> str:
    return f"{target_date.isoformat()}T{snapshot_time}"


def _match_team(session: Session, name: str | None) -> Team | None:
    if not name:
        return None
    canonical = canonicalize_team_name(name)
    return session.execute(
        select(Team).where(Team.canonical_name == canonical)
    ).scalar_one_or_none()


def _match_game(session: Session, home: Team | None, away: Team | None, game_date: date) -> Game | None:
    if not (home and away):
        return None
    return session.execute(
        select(Game)
        .where(
            Game.game_date == game_date,
            Game.home_team_id == home.id,
            Game.away_team_id == away.id,
        )
    ).scalar_one_or_none()


def _ensure_player(session: Session, name: str | None) -> Player | None:
    if not name:
        return None
    canonical = canonicalize_player_name(name)
    values = {"full_name": name, "canonical_name": canonical}
    stmt = insert(Player).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Player.canonical_name],
        set_={"full_name": name},
    ).returning(Player)
    return session.execute(stmt).scalar_one()


def _line_type_for_market(market_key: str) -> str:
    if market_key in {"h2h"}:
        return "moneyline"
    if market_key in {"spreads"}:
        return "spread"
    if market_key in {"totals"}:
        return "total"
    return "prop"


def _side_for_team(outcome_name: str | None, home: Team | None, away: Team | None) -> tuple[str | None, int | None]:
    canonical = canonicalize_team_name(outcome_name)
    if home and canonical == home.canonical_name:
        return "home", home.id
    if away and canonical == away.canonical_name:
        return "away", away.id
    return None, None


def _upsert_odds(
    session: Session,
    game: Game,
    payload: dict,
) -> None:
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
    client: TheOddsApiClient,
    target_date: date,
    settings: OddsSettings,
) -> int:
    snapshot_iso = _snapshot_iso(target_date, settings.snapshot_time)
    events = client.list_historical_events_by_date(snapshot_iso)
    rows = 0
    for event in events:
        home_team = _match_team(session, event.get("home_team"))
        away_team = _match_team(session, event.get("away_team"))
        commence = event.get("commence_time")
        game_dt = datetime.fromisoformat(commence.replace("Z", "+00:00")) if commence else None
        game = _match_game(session, home_team, away_team, game_dt.date() if game_dt else target_date)
        if not game:
            continue
        event_id = event.get("id")
        if event_id and not game.odds_api_event_id:
            session.execute(
                update(Game).where(Game.id == game.id).values(odds_api_event_id=event_id)
            )
        if not event_id:
            continue
        bookmakers = client.historical_event_odds(
            event_id, snapshot_iso, settings.markets, settings.region
        )
        for bookmaker in bookmakers:
            book_key = bookmaker.get("key") or bookmaker.get("title")
            for market in bookmaker.get("markets", []):
                market_key = market.get("key")
                line_type = _line_type_for_market(market_key)
                for outcome in market.get("outcomes", []):
                    if market_key == "totals":
                        participant_type = "total"
                    elif market_key in {"h2h", "spreads"}:
                        participant_type = "team"
                    else:
                        participant_type = "player"
                    participant_name = outcome.get("name") or outcome.get("description")
                    participant_team_id = None
                    participant_player_id = None
                    side = outcome.get("description")
                    if participant_type == "team" and participant_name:
                        side, participant_team_id = _side_for_team(participant_name, home_team, away_team)
                        if not participant_team_id:
                            continue
                        if not side:
                            side = outcome.get("name")
                    elif participant_type == "player" and participant_name:
                        player = _ensure_player(session, participant_name)
                        participant_player_id = player.id if player else None
                        side = outcome.get("description") or outcome.get("type")
                    elif participant_type == "total":
                        side = outcome.get("name") or outcome.get("description")
                    point = outcome.get("point")
                    try:
                        line_value = float(point) if point is not None else None
                    except (TypeError, ValueError):
                        line_value = None
                    last_update_raw = market.get("last_update") or bookmaker.get("last_update")
                    try:
                        last_update_dt = (
                            datetime.fromisoformat(last_update_raw.replace("Z", "+00:00"))
                            if last_update_raw
                            else None
                        )
                    except ValueError:
                        last_update_dt = None
                    payload = {
                        "game_id": game.id,
                        "provider": "odds_api",
                        "bookmaker_key": book_key,
                        "market_key": market_key,
                        "line_type": line_type,
                        "participant_type": participant_type,
                        "participant_name": participant_name or "unknown",
                        "participant_team_id": participant_team_id,
                        "participant_player_id": participant_player_id,
                        "side": side or outcome.get("name"),
                        "line": line_value,
                        "price": outcome.get("price"),
                        "last_update_utc": last_update_dt,
                        "snapshot_ts": datetime.now(timezone.utc),
                        "extra": {
                            "provider_market_key": market_key,
                            "bookmaker": bookmaker.get("title"),
                        },
                    }
                    _upsert_odds(session, game, payload)
                    rows += 1
    return rows


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ingest The Odds API markets into game_odds")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--markets", help="Comma separated markets override")
    args = parser.parse_args(argv)

    markets_override = None
    if args.markets:
        markets_override = [m.strip() for m in args.markets.split(",") if m.strip()]

    settings = _load_settings(markets_override)
    start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end, "%Y-%m-%d").date()

    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    client = TheOddsApiClient(settings.api_key, settings.base_url)

    with get_session(session_factory) as session:
        total_rows = 0
        for target_date in _daterange(start_date, end_date):
            total_rows += ingest_date(session, client, target_date, settings)
        print(f"Inserted/updated {total_rows} odds rows from The Odds API")


if __name__ == "__main__":
    main()

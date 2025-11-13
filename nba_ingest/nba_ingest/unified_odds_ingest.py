"""Unified odds ingestion pipeline orchestrating three providers."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import os
import time
from typing import Iterable

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base
from .odds_helpers import (
    get_ingestion_start_date,
    get_or_create_bookmaker,
    get_or_create_event,
    get_or_create_league,
    get_or_create_player,
    get_or_create_team,
    record_ingestion_state,
    upsert_odds_market,
    parse_datetime,
)


DEFAULT_SGO_START = date(2021, 10, 19)
DEFAULT_SGO_END = date(2023, 5, 2)
DEFAULT_ODDS_API_START = date(2023, 5, 3)
DEFAULT_ODDS_API_END = date(2025, 11, 11)
DEFAULT_BETSAPI_START = date(2021, 10, 19)
DEFAULT_BETSAPI_END = date(2025, 11, 11)


def _require_env(var_name: str) -> str:
    value = os.environ.get(var_name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {var_name}")
    return value


@dataclass
class Settings:
    sportsgameodds_api_key: str
    odds_api_key: str
    betsapi_api_key: str
    database_url: str
    sportsgameodds_base_url: str = "https://api.sportsgameodds.com/v2"
    odds_api_base_url: str = "https://api.the-odds-api.com/v4"
    betsapi_base_url: str = "https://api.betsapi.com/v1"
    markets: tuple[str, ...] = (
        "player_points",
        "player_assists",
        "player_rebounds",
        "player_threes",
    )
    betsapi_markets: tuple[str, ...] = ("moneyline", "spread", "total")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            sportsgameodds_api_key=_require_env("SPORTSGAMEODDS_API_KEY"),
            odds_api_key=_require_env("ODDS_API_KEY"),
            betsapi_api_key=_require_env("BETSAPI_API_KEY"),
            database_url=_require_env("DATABASE_URL"),
        )


class BaseClient:
    def __init__(self, base_url: str, min_interval: float = 1.0, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.min_interval = min_interval
        self.timeout = timeout
        self._last_request_ts = 0.0
        self.session = requests.Session()

    def _sleep_if_needed(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_ts
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_ts = time.monotonic()

    def _request(self, method: str, path: str, *, params: dict | None = None) -> dict:
        self._sleep_if_needed()
        url = f"{self.base_url}{path}"
        response = self.session.request(method, url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()


class SportsGameOddsClient(BaseClient):
    def __init__(self, api_key: str, base_url: str):
        super().__init__(base_url, min_interval=6.5)
        self.api_key = api_key

    def fetch_events_for_date(self, target_date: date) -> list[dict]:
        events: list[dict] = []
        page = 1
        while True:
            params = {
                "apiKey": self.api_key,
                "leagueId": "NBA",
                "startsAfter": target_date.isoformat(),
                "startsBefore": (target_date + timedelta(days=1)).isoformat(),
                "includeAltLines": "true",
                "page": page,
                "limit": 200,
            }
            payload = self._request("GET", "/events", params=params)
            events.extend(payload.get("data", []))
            meta = payload.get("meta") or {}
            if not meta.get("next"):
                break
            page = meta["next"]
        return events


class TheOddsApiClient(BaseClient):
    def __init__(self, api_key: str, base_url: str, sport_key: str = "basketball_nba"):
        super().__init__(base_url, min_interval=2.2)
        self.api_key = api_key
        self.sport_key = sport_key

    def list_events_for_date(self, target_date: date) -> list[dict]:
        params = {
            "apiKey": self.api_key,
            "sport": self.sport_key,
            "dateFormat": "iso",
            "commenceTimeFrom": datetime.combine(target_date, datetime.min.time()).isoformat() + "Z",
            "commenceTimeTo": datetime.combine(target_date + timedelta(days=1), datetime.min.time()).isoformat() + "Z",
        }
        payload = self._request("GET", f"/sports/{self.sport_key}/events", params=params)
        return payload

    def fetch_event_player_props(self, event_id: str, markets: Iterable[str]) -> dict:
        params = {
            "apiKey": self.api_key,
            "markets": ",".join(markets),
        }
        return self._request(
            "GET",
            f"/historical/sports/{self.sport_key}/events/{event_id}/odds",
            params=params,
        )


class BetsApiClient(BaseClient):
    def __init__(self, api_key: str, base_url: str):
        super().__init__(base_url, min_interval=1.5)
        self.api_key = api_key

    def list_events_for_date(self, target_date: date) -> list[dict]:
        params = {
            "token": self.api_key,
            "sport_id": 3,  # NBA
            "day": target_date.strftime("%Y-%m-%d"),
        }
        payload = self._request("GET", "/basketball/matches", params=params)
        return payload.get("results", [])

    def fetch_event_odds(self, event_id: str) -> dict:
        params = {"token": self.api_key, "event_id": event_id}
        return self._request("GET", "/event/odds", params=params)


def daterange(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _coerce_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def ingest_sgo_player_props(
    *,
    session_factory: sessionmaker,
    league_id: int,
    client: SportsGameOddsClient,
    start_date: date,
    end_date: date,
    markets: tuple[str, ...],
) -> None:
    provider = "sportsgameodds"
    if start_date > end_date:
        return
    with session_factory() as session:
        current = get_ingestion_start_date(session, provider, start_date)
        session.commit()
    while current <= end_date:
        events = client.fetch_events_for_date(current)
        print(f"[SGO] {current}: discovered {len(events)} events")
        with session_factory() as session:
            rows_written = 0
            for event_payload in events:
                league_name = (event_payload.get("league") or {}).get("name")
                if league_name and "nba" not in league_name.lower():
                    continue
                start_time = parse_datetime(
                    event_payload.get("startTime")
                    or event_payload.get("startsAt")
                    or event_payload.get("commenceTime")
                )
                home_name = (
                    (event_payload.get("homeTeam") or {}).get("name")
                    or event_payload.get("home")
                )
                away_name = (
                    (event_payload.get("awayTeam") or {}).get("name")
                    or event_payload.get("away")
                )
                if not (home_name and away_name and start_time):
                    continue
                event = get_or_create_event(
                    session,
                    league_id=league_id,
                    start_time_utc=start_time,
                    home_team_name=home_name,
                    away_team_name=away_name,
                    provider_name=provider,
                    provider_event_id=str(event_payload.get("id")),
                )
                rows_written += _persist_sgo_markets(
                    session=session,
                    event_id=event.id,
                    league_id=league_id,
                    markets=markets,
                    payload=event_payload,
                )
            record_ingestion_state(session, provider, current)
            session.commit()
        print(f"[SGO] {current}: wrote {rows_written} markets")
        current += timedelta(days=1)


def _persist_sgo_markets(
    *,
    session: Session,
    event_id: int,
    league_id: int,
    markets: tuple[str, ...],
    payload: dict,
) -> int:
    market_aliases = {
        "player points": "player_points",
        "player point": "player_points",
        "player assists": "player_assists",
        "player rebounds": "player_rebounds",
        "player threes": "player_threes",
        "player 3s": "player_threes",
        "player threes made": "player_threes",
    }
    total_rows = 0
    for book in payload.get("bookmakers", []) or payload.get("books", []) or []:
        book_name = book.get("title") or book.get("name") or book.get("key") or "unknown"
        bookmaker = get_or_create_bookmaker(
            session, "sportsgameodds", book_name, str(book.get("id") or book_name)
        )
        for market in book.get("markets", []):
            raw_category = (market.get("category") or market.get("marketCategory") or "").lower()
            market_type = market_aliases.get(raw_category)
            if not market_type or market_type not in markets:
                continue
            for outcome in market.get("outcomes", []):
                participant = outcome.get("participant") or {}
                player_name = participant.get("name") or market.get("playerName")
                if not player_name:
                    continue
                team = None
                team_name = participant.get("team") or participant.get("teamName")
                if team_name:
                    team = get_or_create_team(
                        session,
                        league_id,
                        team_name,
                        "sportsgameodds",
                        participant.get("teamId"),
                    )
                player = get_or_create_player(
                    session,
                    league_id,
                    team.id if team else None,
                    player_name,
                    "sportsgameodds",
                    participant.get("id"),
                )
                side_label = (outcome.get("type") or outcome.get("label") or "").lower()
                side = "player_over" if "over" in side_label else "player_under"
                line = _coerce_float(outcome.get("line") or outcome.get("points") or outcome.get("point"))
                price = _coerce_int(outcome.get("price") or outcome.get("oddsAmerican"))
                as_of = parse_datetime(
                    outcome.get("updatedAt")
                    or book.get("lastUpdated")
                    or payload.get("updatedAt")
                    or payload.get("startTime")
                    or datetime.utcnow().isoformat()
                )
                upsert_odds_market(
                    session,
                    event_id=event_id,
                    provider="sportsgameodds",
                    market_type=market_type,
                    scope="full_game",
                    participant_type="player",
                    participant_id=player.id,
                    bookmaker_id=bookmaker.id,
                    line=line,
                    price=price,
                    side=side,
                    provider_raw=outcome,
                    as_of=as_of,
                )
                total_rows += 1
    return total_rows


def ingest_odds_api_player_props(
    *,
    session_factory: sessionmaker,
    league_id: int,
    client: TheOddsApiClient,
    start_date: date,
    end_date: date,
    markets: tuple[str, ...],
) -> None:
    provider = "the_odds_api"
    if start_date > end_date:
        return
    with session_factory() as session:
        current = get_ingestion_start_date(session, provider, start_date)
        session.commit()
    while current <= end_date:
        events = client.list_events_for_date(current)
        print(f"[OddsAPI] {current}: {len(events)} events")
        with session_factory() as session:
            total_rows = 0
            for event_payload in events:
                home = event_payload.get("home_team") or event_payload.get("homeTeam")
                away = event_payload.get("away_team") or event_payload.get("awayTeam")
                commence = event_payload.get("commence_time") or event_payload.get("commenceTime")
                if not (home and away and commence):
                    continue
                start_time = parse_datetime(commence)
                event = get_or_create_event(
                    session,
                    league_id=league_id,
                    start_time_utc=start_time,
                    home_team_name=home,
                    away_team_name=away,
                    provider_name=provider,
                    provider_event_id=str(event_payload.get("id")),
                )
                odds_payload = client.fetch_event_player_props(str(event_payload.get("id")), markets)
                total_rows += _persist_odds_api_markets(
                    session=session,
                    event_id=event.id,
                    league_id=league_id,
                    markets=markets,
                    payload=odds_payload,
                )
            record_ingestion_state(session, provider, current)
            session.commit()
        print(f"[OddsAPI] {current}: wrote {total_rows} rows")
        current += timedelta(days=1)


def _persist_odds_api_markets(
    *,
    session: Session,
    event_id: int,
    league_id: int,
    markets: tuple[str, ...],
    payload: dict,
) -> int:
    total_rows = 0
    for bookmaker in payload.get("bookmakers", []):
        book_name = bookmaker.get("title") or bookmaker.get("key")
        book = get_or_create_bookmaker(
            session, "the_odds_api", book_name, bookmaker.get("key")
        )
        for market in bookmaker.get("markets", []):
            key = (market.get("key") or "").lower()
            if key not in markets:
                continue
            for outcome in market.get("outcomes", []):
                player_name = outcome.get("description") or outcome.get("name")
                if not player_name:
                    continue
                player = get_or_create_player(
                    session,
                    league_id,
                    None,
                    player_name,
                    "the_odds_api",
                    outcome.get("playerId"),
                )
                side = outcome.get("name", "").lower()
                resolved_side = "player_over" if "over" in side else "player_under"
                line = _coerce_float(outcome.get("point"))
                price = _coerce_int(outcome.get("price"))
                as_of = parse_datetime(
                    market.get("last_update")
                    or bookmaker.get("last_update")
                    or datetime.utcnow().isoformat()
                )
                upsert_odds_market(
                    session,
                    event_id=event_id,
                    provider="the_odds_api",
                    market_type=key,
                    scope="full_game",
                    participant_type="player",
                    participant_id=player.id,
                    bookmaker_id=book.id,
                    line=line,
                    price=price,
                    side=resolved_side,
                    provider_raw=outcome,
                    as_of=as_of,
                )
                total_rows += 1
    return total_rows


def ingest_betsapi_team_odds(
    *,
    session_factory: sessionmaker,
    league_id: int,
    client: BetsApiClient,
    start_date: date,
    end_date: date,
) -> None:
    provider = "betsapi"
    if start_date > end_date:
        return
    with session_factory() as session:
        current = get_ingestion_start_date(session, provider, start_date)
        session.commit()
    while current <= end_date:
        events = client.list_events_for_date(current)
        print(f"[BetsAPI] {current}: {len(events)} events")
        with session_factory() as session:
            total_rows = 0
            for event in events:
                home = event.get("home") or event.get("homeTeam") or event.get("home_name")
                away = event.get("away") or event.get("awayTeam") or event.get("away_name")
                start_time = event.get("time") or event.get("start_time")
                if not (home and away and start_time):
                    continue
                event_obj = get_or_create_event(
                    session,
                    league_id=league_id,
                    start_time_utc=parse_datetime(start_time),
                    home_team_name=home,
                    away_team_name=away,
                    provider_name=provider,
                    provider_event_id=str(event.get("id") or event.get("event_id")),
                )
                odds_payload = client.fetch_event_odds(str(event.get("id") or event.get("event_id")))
                total_rows += _persist_betsapi_markets(
                    session,
                    event_obj,
                    odds_payload,
                    home,
                    away,
                )
            record_ingestion_state(session, provider, current)
            session.commit()
        print(f"[BetsAPI] {current}: wrote {total_rows} rows")
        current += timedelta(days=1)


def _resolve_team_side(outcome: dict, home_team: str, away_team: str) -> tuple[str, str]:
    label = (outcome.get("name") or outcome.get("label") or outcome.get("team") or "").lower()
    if any(word in label for word in ("home", "h", home_team.lower())):
        return "home", "home"
    if any(word in label for word in ("away", "a", away_team.lower())):
        return "away", "away"
    return "team", label or "team"


def _persist_betsapi_markets(
    session: Session, event: object, payload: dict, home_name: str, away_name: str
) -> int:
    market_map = {
        "moneyline": ["moneyline", "ml", "18_2", "h2h"],
        "spread": ["spread", "handicap", "18_3"],
        "total": ["total", "totals", "18_4"],
    }
    total_rows = 0
    bookmakers = payload.get("bookmakers") or payload.get("results") or []
    for bookmaker in bookmakers:
        name = bookmaker.get("title") or bookmaker.get("name") or bookmaker.get("key")
        book = get_or_create_bookmaker(session, "betsapi", name, str(bookmaker.get("id") or name))
        for market in bookmaker.get("markets", []):
            raw_key = (market.get("key") or market.get("market_name") or "").lower()
            resolved = None
            for key, aliases in market_map.items():
                if raw_key in aliases or any(alias in raw_key for alias in aliases):
                    resolved = key
                    break
            if not resolved:
                continue
            for outcome in market.get("outcomes", []):
                price = _coerce_int(outcome.get("price") or outcome.get("oddsAmerican"))
                line = _coerce_float(outcome.get("point") or outcome.get("line"))
                if resolved == "moneyline":
                    participant_side, _ = _resolve_team_side(
                        outcome,
                        home_name,
                        away_name,
                    )
                    participant_id = event.home_team_id if participant_side == "home" else event.away_team_id
                    side = participant_side
                elif resolved == "spread":
                    participant_side, _ = _resolve_team_side(
                        outcome,
                        home_name,
                        away_name,
                    )
                    participant_id = event.home_team_id if participant_side == "home" else event.away_team_id
                    side = participant_side
                else:
                    participant_id = None
                    side = (outcome.get("name") or outcome.get("label") or "").lower()
                as_of = parse_datetime(
                    outcome.get("updated_at")
                    or market.get("last_update")
                    or bookmaker.get("last_update")
                    or datetime.utcnow().isoformat()
                )
                upsert_odds_market(
                    session,
                    event_id=event.id,
                    provider="betsapi",
                    market_type=resolved,
                    scope="full_game",
                    participant_type="team" if participant_id else "team",
                    participant_id=participant_id,
                    bookmaker_id=book.id,
                    line=line,
                    price=price,
                    side=side,
                    provider_raw=outcome,
                    as_of=as_of,
                )
                total_rows += 1
    return total_rows


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified odds ingestion pipeline")
    parser.add_argument("--start", type=lambda s: date.fromisoformat(s), default=DEFAULT_BETSAPI_START)
    parser.add_argument("--end", type=lambda s: date.fromisoformat(s), default=DEFAULT_BETSAPI_END)
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    settings = Settings.from_env()

    engine = create_engine(settings.database_url, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        league = get_or_create_league(session, "NBA")
        session.commit()
        league_id = league.id

    bets_client = BetsApiClient(settings.betsapi_api_key, settings.betsapi_base_url)
    ingest_betsapi_team_odds(
        session_factory=SessionLocal,
        league_id=league_id,
        client=bets_client,
        start_date=max(DEFAULT_BETSAPI_START, args.start),
        end_date=min(DEFAULT_BETSAPI_END, args.end),
    )

    sgo_client = SportsGameOddsClient(settings.sportsgameodds_api_key, settings.sportsgameodds_base_url)
    ingest_sgo_player_props(
        session_factory=SessionLocal,
        league_id=league_id,
        client=sgo_client,
        start_date=max(DEFAULT_SGO_START, args.start),
        end_date=min(DEFAULT_SGO_END, args.end),
        markets=settings.markets,
    )

    odds_client = TheOddsApiClient(settings.odds_api_key, settings.odds_api_base_url)
    ingest_odds_api_player_props(
        session_factory=SessionLocal,
        league_id=league_id,
        client=odds_client,
        start_date=max(DEFAULT_ODDS_API_START, args.start),
        end_date=min(DEFAULT_ODDS_API_END, args.end),
        markets=settings.markets,
    )


if __name__ == "__main__":  # pragma: no cover
    main()

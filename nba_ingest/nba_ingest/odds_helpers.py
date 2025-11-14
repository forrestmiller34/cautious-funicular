"""Helper utilities for the unified odds ingestion pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
import re
import unicodedata
from typing import Any

from dateutil import parser
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .models import (
    Game,
    IngestionState,
    League,
    OddsBook,
    OddsMarket,
    Player,
    Team,
)


TEAM_CANONICAL: dict[str, dict[str, str]] = {
    "ATL": {"name": "Atlanta Hawks", "short": "Hawks"},
    "BOS": {"name": "Boston Celtics", "short": "Celtics"},
    "BKN": {"name": "Brooklyn Nets", "short": "Nets"},
    "CHA": {"name": "Charlotte Hornets", "short": "Hornets"},
    "CHI": {"name": "Chicago Bulls", "short": "Bulls"},
    "CLE": {"name": "Cleveland Cavaliers", "short": "Cavaliers"},
    "DAL": {"name": "Dallas Mavericks", "short": "Mavericks"},
    "DEN": {"name": "Denver Nuggets", "short": "Nuggets"},
    "DET": {"name": "Detroit Pistons", "short": "Pistons"},
    "GSW": {"name": "Golden State Warriors", "short": "Warriors"},
    "HOU": {"name": "Houston Rockets", "short": "Rockets"},
    "IND": {"name": "Indiana Pacers", "short": "Pacers"},
    "LAC": {"name": "Los Angeles Clippers", "short": "Clippers"},
    "LAL": {"name": "Los Angeles Lakers", "short": "Lakers"},
    "MEM": {"name": "Memphis Grizzlies", "short": "Grizzlies"},
    "MIA": {"name": "Miami Heat", "short": "Heat"},
    "MIL": {"name": "Milwaukee Bucks", "short": "Bucks"},
    "MIN": {"name": "Minnesota Timberwolves", "short": "Timberwolves"},
    "NOP": {"name": "New Orleans Pelicans", "short": "Pelicans"},
    "NYK": {"name": "New York Knicks", "short": "Knicks"},
    "OKC": {"name": "Oklahoma City Thunder", "short": "Thunder"},
    "ORL": {"name": "Orlando Magic", "short": "Magic"},
    "PHI": {"name": "Philadelphia 76ers", "short": "76ers"},
    "PHX": {"name": "Phoenix Suns", "short": "Suns"},
    "POR": {"name": "Portland Trail Blazers", "short": "Trail Blazers"},
    "SAC": {"name": "Sacramento Kings", "short": "Kings"},
    "SAS": {"name": "San Antonio Spurs", "short": "Spurs"},
    "TOR": {"name": "Toronto Raptors", "short": "Raptors"},
    "UTA": {"name": "Utah Jazz", "short": "Jazz"},
    "WAS": {"name": "Washington Wizards", "short": "Wizards"},
}

_TEAM_ALIAS_MAP: dict[str, str] = {
    re.sub(r"[^a-z0-9]", "", abbrev.lower()): abbrev for abbrev in TEAM_CANONICAL
}

TEAM_ADDITIONAL_ALIASES = {
    "losangeleslakers": "LAL",
    "lakers": "LAL",
    "losangelesclippers": "LAC",
    "clippers": "LAC",
    "gswarriors": "GSW",
    "warriors": "GSW",
    "nyknicks": "NYK",
    "knicks": "NYK",
    "newyorkknicks": "NYK",
    "suns": "PHX",
    "sixer": "PHI",
    "76ers": "PHI",
    "boston": "BOS",
    "celtics": "BOS",
    "heat": "MIA",
    "bucks": "MIL",
    "bulls": "CHI",
}
_TEAM_ALIAS_MAP.update(TEAM_ADDITIONAL_ALIASES)


def _canonicalize_string(value: str) -> str:
    normalized = (
        unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    )
    normalized = re.sub(r"[^a-z0-9 ]", "", normalized.lower())
    normalized = re.sub(r"\s+", "", normalized)
    return normalized


def normalize_team_name(raw_name: str) -> str:
    slug = _canonicalize_string(raw_name)
    return _TEAM_ALIAS_MAP.get(slug, raw_name.strip())


def normalize_player_name(full_name: str) -> str:
    slug = (
        unicodedata.normalize("NFKD", full_name)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    slug = re.sub(r"[^a-z0-9 ]", "", slug)
    slug = re.sub(r"\s+", " ", slug).strip()
    return slug


def parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return parser.isoparse(value)


def determine_season(game_date: date) -> str:
    if game_date.month >= 7:
        start_year = game_date.year
        end_year = (game_date.year + 1) % 100
    else:
        start_year = game_date.year - 1
        end_year = game_date.year % 100
    return f"{start_year}-{end_year:02d}"


def get_or_create_league(session: Session, name: str = "NBA") -> League:
    league = session.scalar(select(League).where(League.name == name))
    if league:
        return league
    league = League(name=name, provider_sport_keys={})
    session.add(league)
    session.flush()
    return league


def _update_provider_mapping(target: Any, field: str, provider: str, value: str | None) -> None:
    if not value:
        return
    mapping = getattr(target, field) or {}
    if mapping.get(provider) == value:
        return
    mapping = {**mapping, provider: value}
    setattr(target, field, mapping)


def _provider_lookup(
    session: Session,
    model: type[Team] | type[Player] | type[Game],
    field: str,
    provider: str,
    provider_id: str | None,
    league_id: int,
):
    if not provider_id:
        return None
    for record in session.scalars(select(model).where(model.league_id == league_id)):
        mapping = getattr(record, field) or {}
        if mapping.get(provider) == provider_id:
            return record
    return None


def get_or_create_team(
    session: Session,
    league_id: int,
    raw_name: str,
    provider_name: str,
    provider_team_id: str | None,
) -> Team:
    abbrev = normalize_team_name(raw_name)
    canonical = TEAM_CANONICAL.get(
        abbrev,
        {"name": raw_name.strip(), "short": raw_name.strip().split(" ")[-1][:32]},
    )

    team = _provider_lookup(
        session, Team, "provider_team_ids", provider_name, provider_team_id, league_id
    )
    if not team:
        team = session.scalar(
            select(Team).where(Team.league_id == league_id, Team.abbrev == abbrev)
        )
    if not team:
        team = Team(
            league_id=league_id,
            name=canonical["name"],
            short_name=canonical.get("short"),
            abbrev=abbrev if isinstance(abbrev, str) else canonical["name"][:3].upper(),
            canonical_name=canonical["name"].lower(),
        )
        session.add(team)
        session.flush()

    _update_provider_mapping(team, "provider_team_ids", provider_name, provider_team_id)
    return team


def get_or_create_player(
    session: Session,
    league_id: int,
    team_id: int | None,
    full_name: str,
    provider_name: str,
    provider_player_id: str | None,
) -> Player:
    canonical = normalize_player_name(full_name)
    player = _provider_lookup(
        session, Player, "provider_player_ids", provider_name, provider_player_id, league_id
    )
    if not player:
        player = session.scalar(
            select(Player).where(Player.league_id == league_id, Player.canonical_name == canonical)
        )
    if not player:
        player = Player(
            league_id=league_id,
            team_id=team_id,
            full_name=full_name,
            canonical_name=canonical,
        )
        session.add(player)
        session.flush()
    elif team_id and player.team_id is None:
        player.team_id = team_id

    _update_provider_mapping(player, "provider_player_ids", provider_name, provider_player_id)
    return player


def get_or_create_event(
    session: Session,
    league_id: int,
    start_time_utc: datetime,
    home_team_name: str,
    away_team_name: str,
    provider_name: str,
    provider_event_id: str | None,
) -> Game:
    game_date = start_time_utc.date()
    season = determine_season(game_date)
    home_team = get_or_create_team(session, league_id, home_team_name, provider_name, None)
    away_team = get_or_create_team(session, league_id, away_team_name, provider_name, None)

    event = _provider_lookup(
        session, Game, "provider_event_ids", provider_name, provider_event_id, league_id
    )
    if not event:
        tolerance = timedelta(hours=2)
        start_min = start_time_utc - tolerance
        start_max = start_time_utc + tolerance
        event = session.scalar(
            select(Game)
            .where(Game.league_id == league_id)
            .where(Game.home_team_id == home_team.id)
            .where(Game.away_team_id == away_team.id)
            .where(Game.start_time_utc >= start_min)
            .where(Game.start_time_utc <= start_max)
        )
    if not event:
        event = Game(
            league_id=league_id,
            game_date=game_date,
            season=season,
            season_type="regular",
            home_team_id=home_team.id,
            away_team_id=away_team.id,
            start_time_utc=start_time_utc,
        )
        session.add(event)
        session.flush()
    else:
        event.start_time_utc = event.start_time_utc or start_time_utc
        event.game_date = event.game_date or game_date
        event.season = event.season or season

    _update_provider_mapping(event, "provider_event_ids", provider_name, provider_event_id)
    return event


def get_or_create_bookmaker(
    session: Session, provider_name: str, name: str, provider_id: str | None = None
) -> OddsBook:
    bookmaker = session.scalar(select(OddsBook).where(OddsBook.name == name))
    if not bookmaker:
        bookmaker = OddsBook(name=name, provider_ids={})
        session.add(bookmaker)
        session.flush()
    _update_provider_mapping(bookmaker, "provider_ids", provider_name, provider_id or name)
    return bookmaker


def _normalize_numeric(value: float | Decimal | None) -> str:
    if value is None:
        return "null"
    if isinstance(value, Decimal):
        normalized = value
    else:
        normalized = Decimal(str(value))
    return f"{normalized:.3f}"


def _build_identity_key(
    *,
    event_id: int,
    provider: str,
    bookmaker_id: int,
    market_type: str,
    scope: str,
    participant_type: str,
    participant_id: int | None,
    side: str | None,
    line: float | Decimal | None,
) -> str:
    parts = [
        str(event_id),
        provider,
        str(bookmaker_id),
        market_type,
        scope,
        participant_type,
        str(participant_id) if participant_id is not None else "null",
        (side or "null").lower(),
        _normalize_numeric(line),
    ]
    return "|".join(parts)


def upsert_odds_market(
    session: Session,
    *,
    event_id: int,
    provider: str,
    market_type: str,
    scope: str,
    participant_type: str,
    participant_id: int | None,
    bookmaker_id: int,
    line: float | None,
    price: int | None,
    side: str | None,
    provider_raw: dict | None,
    as_of: datetime,
) -> None:
    identity_key = _build_identity_key(
        event_id=event_id,
        provider=provider,
        bookmaker_id=bookmaker_id,
        market_type=market_type,
        scope=scope,
        participant_type=participant_type,
        participant_id=participant_id,
        side=side,
        line=line,
    )
    stmt = pg_insert(OddsMarket).values(
        event_id=event_id,
        provider=provider,
        market_type=market_type,
        scope=scope,
        participant_type=participant_type,
        participant_id=participant_id,
        bookmaker_id=bookmaker_id,
        line=line,
        price=price,
        side=side,
        provider_raw=provider_raw,
        as_of=as_of,
        identity_key=identity_key,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_odds_markets_identity",
        set_={
            "line": stmt.excluded.line,
            "price": stmt.excluded.price,
            "side": stmt.excluded.side,
            "provider_raw": stmt.excluded.provider_raw,
            "as_of": stmt.excluded.as_of,
            "identity_key": stmt.excluded.identity_key,
        },
    )
    session.execute(stmt)


def get_ingestion_start_date(session: Session, provider: str, target_start: date) -> date:
    state = session.get(IngestionState, provider)
    if state and state.last_successful_date and state.last_successful_date >= target_start:
        return state.last_successful_date + timedelta(days=1)
    return target_start


def record_ingestion_state(session: Session, provider: str, processed_date: date) -> None:
    state = session.get(IngestionState, provider)
    if not state:
        state = IngestionState(provider=provider, last_successful_date=processed_date)
        session.add(state)
    elif not state.last_successful_date or processed_date > state.last_successful_date:
        state.last_successful_date = processed_date


@dataclass
class OddsRow:
    provider: str
    market_type: str
    scope: str
    participant_type: str
    participant_id: int | None
    bookmaker_id: int
    line: float | None
    price: int | None
    side: str | None
    provider_raw: dict | None
    as_of: datetime


__all__ = [
    "determine_season",
    "get_ingestion_start_date",
    "get_or_create_bookmaker",
    "get_or_create_event",
    "get_or_create_league",
    "get_or_create_player",
    "get_or_create_team",
    "normalize_player_name",
    "normalize_team_name",
    "OddsRow",
    "parse_datetime",
    "record_ingestion_state",
    "upsert_odds_market",
]

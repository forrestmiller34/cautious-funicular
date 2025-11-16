"""Scrape Basketball Reference play-by-play data into the warehouse."""
from __future__ import annotations

import argparse
from collections.abc import Iterable
from datetime import date, datetime, timedelta
import time
from typing import Any

from basketball_reference_web_scraper import client
from basketball_reference_web_scraper.data import OutputType, Team as BrefTeam
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from .db import create_db_engine, create_session_factory, get_session
from .models import Base, Game, PlayByPlayEvent, Team as DBTeam
from .nba_api_ingest_pbp import _load_database_url, log


DATE_FORMAT = "%Y-%m-%d"

BREF_TEAM_CANONICAL = {
    BrefTeam.ATLANTA_HAWKS: ("ATL", "Atlanta Hawks"),
    BrefTeam.BOSTON_CELTICS: ("BOS", "Boston Celtics"),
    BrefTeam.BROOKLYN_NETS: ("BKN", "Brooklyn Nets"),
    BrefTeam.CHARLOTTE_HORNETS: ("CHA", "Charlotte Hornets"),
    BrefTeam.CHICAGO_BULLS: ("CHI", "Chicago Bulls"),
    BrefTeam.CLEVELAND_CAVALIERS: ("CLE", "Cleveland Cavaliers"),
    BrefTeam.DALLAS_MAVERICKS: ("DAL", "Dallas Mavericks"),
    BrefTeam.DENVER_NUGGETS: ("DEN", "Denver Nuggets"),
    BrefTeam.DETROIT_PISTONS: ("DET", "Detroit Pistons"),
    BrefTeam.GOLDEN_STATE_WARRIORS: ("GSW", "Golden State Warriors"),
    BrefTeam.HOUSTON_ROCKETS: ("HOU", "Houston Rockets"),
    BrefTeam.INDIANA_PACERS: ("IND", "Indiana Pacers"),
    BrefTeam.LOS_ANGELES_CLIPPERS: ("LAC", "Los Angeles Clippers"),
    BrefTeam.LOS_ANGELES_LAKERS: ("LAL", "Los Angeles Lakers"),
    BrefTeam.MEMPHIS_GRIZZLIES: ("MEM", "Memphis Grizzlies"),
    BrefTeam.MIAMI_HEAT: ("MIA", "Miami Heat"),
    BrefTeam.MILWAUKEE_BUCKS: ("MIL", "Milwaukee Bucks"),
    BrefTeam.MINNESOTA_TIMBERWOLVES: ("MIN", "Minnesota Timberwolves"),
    BrefTeam.NEW_ORLEANS_PELICANS: ("NOP", "New Orleans Pelicans"),
    BrefTeam.NEW_YORK_KNICKS: ("NYK", "New York Knicks"),
    BrefTeam.OKLAHOMA_CITY_THUNDER: ("OKC", "Oklahoma City Thunder"),
    BrefTeam.ORLANDO_MAGIC: ("ORL", "Orlando Magic"),
    BrefTeam.PHILADELPHIA_76ERS: ("PHI", "Philadelphia 76ers"),
    BrefTeam.PHOENIX_SUNS: ("PHX", "Phoenix Suns"),
    BrefTeam.PORTLAND_TRAIL_BLAZERS: ("POR", "Portland Trail Blazers"),
    BrefTeam.SACRAMENTO_KINGS: ("SAC", "Sacramento Kings"),
    BrefTeam.SAN_ANTONIO_SPURS: ("SAS", "San Antonio Spurs"),
    BrefTeam.TORONTO_RAPTORS: ("TOR", "Toronto Raptors"),
    BrefTeam.UTAH_JAZZ: ("UTA", "Utah Jazz"),
    BrefTeam.WASHINGTON_WIZARDS: ("WAS", "Washington Wizards"),
}


ScheduleCache = dict[int, list[dict[str, Any]]]
TeamIdCache = dict[BrefTeam, int | None]


def _parse_date(value: str) -> date:
    return datetime.strptime(value, DATE_FORMAT).date()


def _daterange(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _season_end_year_for_date(game_date: date) -> int:
    return game_date.year + 1 if game_date.month >= 7 else game_date.year


def _load_schedule(season_end_year: int, cache: ScheduleCache) -> list[dict[str, Any]]:
    if season_end_year not in cache:
        log(f"Downloading schedule for {season_end_year} season from Basketball Reference…")
        try:
            cache[season_end_year] = client.season_schedule(
                season_end_year=season_end_year
            )
        except Exception as exc:  # noqa: BLE001
            log(
                f"Failed to download Basketball Reference schedule for {season_end_year}: {exc!r}"
            )
            return []
    return cache.get(season_end_year, [])


def _resolve_bref_team_id(session: Session, bref_team: BrefTeam, cache: TeamIdCache) -> int | None:
    if bref_team in cache:
        return cache[bref_team]

    team_info = BREF_TEAM_CANONICAL.get(bref_team)
    abbrev = team_info[0] if team_info else None
    full_name = team_info[1] if team_info else None
    derived_name = bref_team.name.replace("_", " ").title()

    filters = []
    if abbrev:
        filters.append(DBTeam.abbrev == abbrev)
    if full_name:
        filters.append(DBTeam.name == full_name)
    if derived_name:
        filters.append(DBTeam.name == derived_name)

    team_id = None
    if filters:
        stmt = select(DBTeam.id)
        if len(filters) == 1:
            stmt = stmt.where(filters[0])
        else:
            stmt = stmt.where(or_(*filters))
        team_id = session.execute(stmt).scalar_one_or_none()

    cache[bref_team] = team_id
    if not team_id:
        log(f"Unable to map Basketball Reference team {bref_team} to warehouse team.")
    return team_id


def _find_game_record(
    session: Session,
    target_date: date,
    home_team_id: int,
    away_team_id: int,
) -> Game | None:
    stmt = select(Game).where(
        Game.game_date == target_date,
        Game.home_team_id == home_team_id,
        Game.away_team_id == away_team_id,
    )
    return session.execute(stmt).scalar_one_or_none()


def _format_clock(event: dict[str, Any]) -> str | None:
    minutes = event.get("minutes_remaining_in_period")
    seconds = event.get("seconds_remaining_in_period")
    if minutes is not None and seconds is not None:
        try:
            return f"{int(minutes)}:{int(seconds):02d}"
        except (TypeError, ValueError):
            pass
    clock = event.get("clock") or event.get("time_remaining")
    if isinstance(clock, str):
        return clock.strip()
    return None


def _event_team_id(event: dict[str, Any], cache: TeamIdCache) -> int | None:
    team = event.get("team")
    if isinstance(team, BrefTeam):
        return cache.get(team)
    if isinstance(team, str):
        normalized = team.replace(" ", "_").upper()
        for bref_team in cache:
            if bref_team.name == normalized:
                return cache[bref_team]
        for bref_team, (_, full_name) in BREF_TEAM_CANONICAL.items():
            if full_name.lower() == team.strip().lower():
                return cache.get(bref_team)
    return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _serialize_event(event: dict[str, Any]) -> dict[str, Any]:
    def transform(value: Any) -> Any:
        if isinstance(value, BrefTeam):
            return value.name
        if isinstance(value, dict):
            return {k: transform(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [transform(v) for v in value]
        return value

    return {key: transform(val) for key, val in event.items()}


def _build_event_payload(
    event: dict[str, Any],
    game_id: int,
    event_index: int,
    team_cache: TeamIdCache,
) -> dict[str, Any]:
    description = event.get("description") or event.get("event_description") or event.get("play_description")
    payload: dict[str, Any] = {
        "game_id": game_id,
        "event_num": event_index,
        "period": _to_int(event.get("period")),
        "clock": _format_clock(event),
        "event_type": event.get("event_type") or event.get("action_type"),
        "description": description,
        "home_score": _to_int(event.get("home_score")),
        "away_score": _to_int(event.get("away_score")),
        "team_id": _event_team_id(event, team_cache),
        "raw_json": _serialize_event(event),
    }
    return payload


def _ingest_game_events(
    session: Session,
    bref_home_team: BrefTeam,
    bref_away_team: BrefTeam,
    game: Game,
    game_date: date,
    delay: float,
    team_cache: TeamIdCache,
) -> int:
    log(
        f"{game_date}: fetching Basketball Reference play-by-play for {bref_away_team.name} @ {bref_home_team.name}"
    )
    try:
        events = client.play_by_play(
            home_team=bref_home_team,
            year=game_date.year,
            month=game_date.month,
            day=game_date.day,
            output_type=OutputType.JSON,
        )
    except Exception as exc:  # noqa: BLE001
        log(
            f"{game_date}: failed to fetch play-by-play for game {game.id} ({bref_home_team}): {exc!r}"
        )
        return 0

    records = []
    for index, event in enumerate(events, start=1):
        payload = _build_event_payload(event, game.id, index, team_cache)
        records.append(PlayByPlayEvent(**payload))

    if not records:
        log(f"{game_date}: Basketball Reference returned zero events for game {game.id}")
        return 0

    session.execute(delete(PlayByPlayEvent).where(PlayByPlayEvent.game_id == game.id))
    session.add_all(records)
    session.commit()
    log(f"Inserted {len(records)} play-by-play rows for game {game.id}")
    if delay > 0:
        time.sleep(delay)
    return len(records)


def _process_date(
    session: Session,
    current_date: date,
    schedule_cache: ScheduleCache,
    team_cache: TeamIdCache,
    delay: float,
) -> tuple[int, int]:
    season_end_year = _season_end_year_for_date(current_date)
    schedule = _load_schedule(season_end_year, schedule_cache)
    games = [g for g in schedule if g.get("start_time") and g["start_time"].date() == current_date]
    if not games:
        log(f"{current_date}: no Basketball Reference games scheduled")
        return 0, 0

    date_events = 0
    date_games = 0
    for matchup in games:
        home_team_enum = matchup.get("home_team")
        away_team_enum = matchup.get("away_team")
        if not isinstance(home_team_enum, BrefTeam) or not isinstance(away_team_enum, BrefTeam):
            log(f"{current_date}: skipping matchup with invalid team payload: {matchup}")
            continue
        home_team_id = _resolve_bref_team_id(session, home_team_enum, team_cache)
        away_team_id = _resolve_bref_team_id(session, away_team_enum, team_cache)
        if not (home_team_id and away_team_id):
            continue
        game = _find_game_record(session, current_date, home_team_id, away_team_id)
        if not game:
            log(
                f"{current_date}: no warehouse game row for {away_team_enum.name} @ {home_team_enum.name}; skipping"
            )
            continue
        ingested = _ingest_game_events(
            session,
            bref_home_team=home_team_enum,
            bref_away_team=away_team_enum,
            game=game,
            game_date=current_date,
            delay=delay,
            team_cache=team_cache,
        )
        if ingested:
            date_games += 1
            date_events += ingested
    log(f"{current_date}: ingested {date_events} events across {date_games} games")
    return date_games, date_events


def main(argv: Iterable[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Ingest Basketball Reference play-by-play for a date range"
    )
    parser.add_argument("--start", required=True, help="Inclusive start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="Inclusive end date (YYYY-MM-DD)")
    parser.add_argument(
        "--delay",
        type=float,
        default=3.0,
        help="Delay in seconds between games to respect Basketball Reference",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    start_date = _parse_date(args.start)
    end_date = _parse_date(args.end)
    if start_date > end_date:
        raise ValueError("Start date must be on or before end date")

    database_url = _load_database_url()
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)

    schedule_cache: ScheduleCache = {}
    team_cache: TeamIdCache = {}

    total_dates = 0
    total_games = 0
    total_events = 0

    with get_session(session_factory) as session:
        for current_date in _daterange(start_date, end_date):
            total_dates += 1
            games, events = _process_date(session, current_date, schedule_cache, team_cache, args.delay)
            total_games += games
            total_events += events

    log(
        f"Basketball Reference ingestion complete: {total_dates} dates, {total_games} games, {total_events} events"
    )


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()

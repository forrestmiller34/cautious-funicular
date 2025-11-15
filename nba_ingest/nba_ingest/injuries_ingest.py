"""CLI entry point for ingesting NBA injury reports via RapidAPI."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import time
from typing import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from .config import load_injuries_settings
from .db import create_db_engine, create_session_factory, get_session
from .injuries_client import NBAInjuriesClient
from .models import Base, IngestionState, Injury, League, Player, Team
from .normalization import canonicalize_player_name, canonicalize_team_name

INGESTION_PROVIDER = "nba_injuries_reports"
REQUEST_DELAY_SECONDS = 0.3
TEAM_NAME_ALIASES = {
    "la clippers": "los angeles clippers",
    "l.a. clippers": "los angeles clippers",
    "la lakers": "los angeles lakers",
    "l.a. lakers": "los angeles lakers",
    "ny knicks": "new york knicks",
    "nyc knicks": "new york knicks",
    "gs warriors": "golden state warriors",
    "sa spurs": "san antonio spurs",
}


def log(message: str) -> None:
    print(f"[NBA-INJ] {message}", flush=True)


def _parse_cli_date(parser: argparse.ArgumentParser, value: str, flag: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        parser.error(f"Invalid {flag} date '{value}', expected YYYY-MM-DD")
        raise


def _build_team_lookup(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(
            Team.id,
            Team.canonical_name,
            Team.name,
            Team.short_name,
            Team.abbrev,
            Team.city,
        )
    ).all()
    lookup: dict[str, int] = {}
    canonical_to_id: dict[str, int] = {}
    for row in rows:
        team_id, canonical_name, name, short_name, abbrev, city = row
        canonical_norm = canonicalize_team_name(canonical_name)
        canonical_to_id[canonical_norm] = team_id
        candidates = {
            canonical_norm,
            canonicalize_team_name(name),
            canonicalize_team_name(short_name),
            canonicalize_team_name(abbrev),
        }
        if city and name:
            candidates.add(canonicalize_team_name(f"{city} {name}"))
        for candidate in candidates:
            if candidate and candidate != "unknown":
                lookup.setdefault(candidate, team_id)

    for alias, canonical in TEAM_NAME_ALIASES.items():
        alias_norm = canonicalize_team_name(alias)
        canonical_norm = canonicalize_team_name(canonical)
        team_id = lookup.get(canonical_norm) or canonical_to_id.get(canonical_norm)
        if team_id and alias_norm:
            lookup.setdefault(alias_norm, team_id)

    return lookup


def _build_player_lookup(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(
            Player.id,
            Player.canonical_name,
            Player.full_name,
        )
    ).all()
    lookup: dict[str, int] = {}
    for player_id, canonical_name, full_name in rows:
        candidates = {
            canonicalize_player_name(canonical_name),
            canonicalize_player_name(full_name),
        }
        for candidate in candidates:
            if candidate and candidate != "unknown":
                lookup.setdefault(candidate, player_id)
    return lookup


def _match_team_id(team_name: str | None, lookup: dict[str, int]) -> int | None:
    if not team_name:
        return None
    normalized = canonicalize_team_name(team_name)
    normalized = TEAM_NAME_ALIASES.get(normalized, normalized)
    return lookup.get(normalized)


def _match_player_id(player_name: str | None, lookup: dict[str, int]) -> int | None:
    if not player_name:
        return None
    normalized = canonicalize_player_name(player_name)
    return lookup.get(normalized)


def _parse_injury_date(value: str | None) -> date:
    if not value:
        raise ValueError("Injury payload missing date")
    return datetime.strptime(value, "%Y-%m-%d").date()


def _upsert_injury(
    session: Session,
    *,
    league_id: int | None,
    team_lookup: dict[str, int],
    player_lookup: dict[str, int],
    payload: dict,
) -> None:
    injury_date = _parse_injury_date(payload.get("date"))
    team_name = (payload.get("team") or "").strip() or "Unknown"
    player_name = (payload.get("player") or "").strip() or "Unknown"
    status = (payload.get("status") or "").strip() or None
    reason = (payload.get("reason") or "").strip() or None
    report_time = (payload.get("reportTime") or "").strip() or None
    team_id = _match_team_id(team_name, team_lookup)
    player_id = _match_player_id(player_name, player_lookup)
    values = {
        "league_id": league_id,
        "injury_date": injury_date,
        "team_name": team_name,
        "player_name": player_name,
        "status": status,
        "reason": reason,
        "report_time_raw": report_time,
        "raw_json": payload,
        "player_id": player_id,
        "team_id": team_id,
    }
    stmt = (
        insert(Injury)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_injuries_snapshot",
            set_={
                "status": values["status"],
                "reason": values["reason"],
                "report_time_raw": values["report_time_raw"],
                "raw_json": values["raw_json"],
                "player_id": values["player_id"],
                "team_id": values["team_id"],
                "updated_at": func.now(),
            },
        )
    )
    session.execute(stmt)


def _update_ingestion_state(session: Session, as_of: date) -> None:
    stmt = (
        insert(IngestionState)
        .values(provider=INGESTION_PROVIDER, last_successful_date=as_of)
        .on_conflict_do_update(
            index_elements=[IngestionState.provider],
            set_={"last_successful_date": as_of},
        )
    )
    session.execute(stmt)


def _determine_range(
    session: Session,
    start_date: date | None,
    end_date: date | None,
) -> tuple[date, date]:
    state = session.get(IngestionState, INGESTION_PROVIDER)
    if start_date is None:
        if state and state.last_successful_date:
            start_date = state.last_successful_date + timedelta(days=1)
        else:
            start_date = date.today()
    if end_date is None:
        end_date = date.today()
    if end_date < start_date:
        raise ValueError("End date cannot be earlier than start date")
    return start_date, end_date


def _daterange(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def ingest_injuries(
    session: Session,
    client: NBAInjuriesClient,
    *,
    start_date: date,
    end_date: date,
) -> int:
    team_lookup = _build_team_lookup(session)
    player_lookup = _build_player_lookup(session)
    league_id = session.execute(select(League.id).where(League.name == "NBA")).scalar_one_or_none()
    total_rows = 0

    for current_date in _daterange(start_date, end_date):
        log(f"Fetching injuries for {current_date.isoformat()}...")
        payloads = client.get_injuries_for_date(current_date)
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            _upsert_injury(
                session,
                league_id=league_id,
                team_lookup=team_lookup,
                player_lookup=player_lookup,
                payload=payload,
            )
            total_rows += 1
        _update_ingestion_state(session, current_date)
        session.commit()
        time.sleep(REQUEST_DELAY_SECONDS)
    return total_rows


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ingest NBA injuries from RapidAPI")
    parser.add_argument("--start", type=str, help="Start date YYYY-MM-DD", required=False)
    parser.add_argument("--end", type=str, help="End date YYYY-MM-DD", required=False)
    args = parser.parse_args(argv)

    if bool(args.start) ^ bool(args.end):
        parser.error("--start and --end must be provided together")

    start_date = _parse_cli_date(parser, args.start, "--start") if args.start else None
    end_date = _parse_cli_date(parser, args.end, "--end") if args.end else None

    settings = load_injuries_settings()
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    client = NBAInjuriesClient(
        api_key=settings.api_key,
        host=settings.host,
        base_url=settings.base_url,
    )

    with get_session(session_factory) as session:
        start_bound, end_bound = _determine_range(session, start_date, end_date)
        log(
            f"Starting injuries ingest from {start_bound} to {end_bound} "
            f"(provider={INGESTION_PROVIDER})."
        )
        rows = ingest_injuries(
            session,
            client,
            start_date=start_bound,
            end_date=end_bound,
        )
        log(
            f"Completed injuries ingest for {start_bound} - {end_bound}: "
            f"rows={rows}."
        )


if __name__ == "__main__":
    main()

"""Ingest nba_api scoreboard mappings and play-by-play data into Postgres."""
from __future__ import annotations

import argparse
import os
import time
from datetime import date, datetime, timedelta
from typing import Iterable, Sequence

import pandas as pd
from dotenv import load_dotenv
from nba_api.stats.endpoints import PlayByPlayV2, ScoreboardV2
from nba_api.stats.static import teams as static_teams
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .db import create_db_engine, create_session_factory, get_session
from .models import Base, Game, PlayByPlayEvent, Player, Team
from .odds_helpers import get_ingestion_start_date, record_ingestion_state
from .normalization import canonicalize_player_name
from .notifications import notify


def log(message: str) -> None:
    print(f"[NBA_PBP] {message}", flush=True)


def _load_database_url() -> str:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required for nba_api_ingest_pbp")
    return url


def _daterange(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _parse_date(value: str | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    return pd.to_datetime(value).date()

TEAMS_BY_ID: dict[str, dict] = {}

def _get_teams_by_id() -> dict[str, dict]:
    global TEAMS_BY_ID
    if not TEAMS_BY_ID:
        TEAMS_BY_ID = {str(team["id"]): team for team in static_teams.get_teams()}
    return TEAMS_BY_ID

def _resolve_team_id(session: Session, nba_team_id: int | None, abbreviation: str | None) -> int | None:
    if nba_team_id:
        nba_team_id_str = str(int(nba_team_id))
        team_id = session.execute(
            select(Team.id).where(Team.nba_team_id == nba_team_id_str)
        ).scalar_one_or_none()
        if team_id:
            return team_id
        teams_by_id = _get_teams_by_id()
        team_info = teams_by_id.get(nba_team_id_str)
        if team_info:
            abbreviation = team_info.get("abbreviation") or abbreviation
    if abbreviation:
        return session.execute(
            select(Team.id).where(Team.abbrev == abbreviation.upper())
        ).scalar_one_or_none()
    return None

def _ensure_player(session: Session, nba_id: int | None, name: str | None) -> int | None:
    if not name:
        return None
    canonical = canonicalize_player_name(name)
    values = {
        "full_name": name,
        "canonical_name": canonical,
        "nba_player_id": str(nba_id) if nba_id else None,
    }
    stmt = insert(Player).values(**values)
    if nba_id:
        stmt = stmt.on_conflict_do_update(
            constraint="uq_players_nba_player_id",
            set_={"full_name": name, "canonical_name": canonical},
        )
    else:
        stmt = stmt.on_conflict_do_update(
            index_elements=[Player.canonical_name],
            set_={"full_name": name},
        )
    stmt = stmt.returning(Player.id)
    return session.execute(stmt).scalar_one()


def _map_scoreboard_games(session: Session, target_date: date) -> int:
    sb = ScoreboardV2(game_date=target_date.strftime("%Y-%m-%d"),
    )
    games_df = sb.game_header.get_data_frame()
    if games_df.empty:
        return 0
    mapped = 0
    for _, row in games_df.iterrows():
        nba_game_id = row.get("GAME_ID")
        game_date = _parse_date(row.get("GAME_DATE_EST"))
        home_abbrev = row.get("HOME_TEAM_ABBREVIATION")
        away_abbrev = row.get("VISITOR_TEAM_ABBREVIATION")
        home_team_id = _resolve_team_id(session, row.get("HOME_TEAM_ID"), home_abbrev)
        away_team_id = _resolve_team_id(session, row.get("VISITOR_TEAM_ID"), away_abbrev)
        log(
        f"Scoreboard row {game_date} {away_abbrev}@{home_abbrev}: "
        f"nba_game_id={nba_game_id}, home_team_id={home_team_id}, away_team_id={away_team_id}"
        )
        if home_team_id and row.get("HOME_TEAM_ID"):
            session.execute(
                update(Team)
                .where(Team.id == home_team_id, Team.nba_team_id.is_(None))
                .values(nba_team_id=str(int(row["HOME_TEAM_ID"])))
            )
        if away_team_id and row.get("VISITOR_TEAM_ID"):
            session.execute(
                update(Team)
                .where(Team.id == away_team_id, Team.nba_team_id.is_(None))
                .values(nba_team_id=str(int(row["VISITOR_TEAM_ID"])))
            )
        if not (home_team_id and away_team_id and nba_game_id):
            continue
        game = session.execute(
            select(Game)
            .where(
                Game.game_date == game_date,
                Game.home_team_id == home_team_id,
                Game.away_team_id == away_team_id,
            )
        ).scalar_one_or_none()
        if not game:
            continue
        if not game.nba_game_id:
            session.execute(
                update(Game).where(Game.id == game.id).values(nba_game_id=nba_game_id)
            )
        mapped += 1
    return mapped


def _ingest_play_by_play(session: Session, game: Game, delay: float) -> int:
    if not game.nba_game_id:
        log(f"{game.game_date}: skipping game {game.id} (missing NBA game id).")
        return 0
    pbp = PlayByPlayV2(game_id=game.nba_game_id)
    frames = pbp.get_data_frames()
    if not frames:
        log(f"{game.game_date}: skipping game {game.nba_game_id} (no play-by-play data).")
        return 0
    df = frames[0]
    inserted = 0
    home_team_name = getattr(game.home_team, "name", f"home_id={game.home_team_id}")
    away_team_name = getattr(game.away_team, "name", f"away_id={game.away_team_id}")
    log(
        f"{game.game_date}: fetching PBP for game {game.nba_game_id} "
        f"({home_team_name} vs {away_team_name})..."
    )
    for _, row in df.iterrows():
        event_num = int(row.get("EVENTNUM"))
        description = row.get("HOMEDESCRIPTION") or row.get("VISITORDESCRIPTION") or row.get("NEUTRALDESCRIPTION")
        team_id = _resolve_team_id(session, row.get("PLAYER1_TEAM_ID"), row.get("PLAYER1_TEAM_ABBREVIATION"))
        player1_id = _ensure_player(session, row.get("PLAYER1_ID"), row.get("PLAYER1_NAME"))
        player2_id = _ensure_player(session, row.get("PLAYER2_ID"), row.get("PLAYER2_NAME"))
        player3_id = _ensure_player(session, row.get("PLAYER3_ID"), row.get("PLAYER3_NAME"))
        payload = {
            "game_id": game.id,
            "event_num": event_num,
            "period": row.get("PERIOD"),
            "clock": row.get("PCTIMESTRING"),
            "event_type": str(row.get("EVENTMSGTYPE")),
            "description": description,
            "team_id": team_id,
            "player1_id": player1_id,
            "player2_id": player2_id,
            "player3_id": player3_id,
            "home_score": row.get("HOMESCORE"),
            "away_score": row.get("VISITORSCORE"),
            "raw_json": {k: (None if pd.isna(v) else v) for k, v in row.items()},
        }
        stmt = (
            insert(PlayByPlayEvent)
            .values(**payload)
            .on_conflict_do_update(
                constraint="uq_pbp_game_event",
                set_={
                    "description": payload["description"],
                    "home_score": payload["home_score"],
                    "away_score": payload["away_score"],
                    "raw_json": payload["raw_json"],
                },
            )
        )
        session.execute(stmt)
        inserted += 1
    time.sleep(delay)
    log(
        f"{game.game_date}: stored {inserted} PBP events for game {game.nba_game_id}."
    )
    return inserted


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Map nba_api games and ingest play-by-play")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between pbp API calls")
    args = parser.parse_args(argv)

    start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end, "%Y-%m-%d").date()
    log(
        f"Starting NBA play-by-play ingest from {start_date} to {end_date} "
        f"with delay={args.delay}s."
    )
    provider_name = "nba_api_pbp"
    database_url = _load_database_url()
    log(f"NBA PBP ingest using DATABASE_URL={database_url}")
    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)

    with get_session(session_factory) as session:
        effective_start = get_ingestion_start_date(session, provider_name, start_date)
        if effective_start > end_date:
            log(
                f"All dates up to {end_date} already ingested for provider {provider_name}; nothing to do."
            )
            return

        num_dates = 0
        total_events = 0
        try:
            for target_date in _daterange(effective_start, end_date):
                log(f"Processing date {target_date}: syncing scoreboard...")
                mapped = _map_scoreboard_games(session, target_date)
                log(f"Processing date {target_date}: mapped {mapped} games from scoreboard.")
                games_for_date = list(
                    session.execute(
                        select(Game).where(
                            Game.nba_game_id.is_not(None),
                            Game.game_date == target_date,
                        )
                    ).scalars()
                )
                if not games_for_date:
                    log(f"Processing date {target_date}: no games found.")
                for idx, game in enumerate(games_for_date, start=1):
                    total_events += _ingest_play_by_play(session, game, args.delay)
                    if idx % 5 == 0 or idx == len(games_for_date):
                        log(
                            f"Processing date {target_date}: processed {idx}/{len(games_for_date)} games; "
                            f"total_events={total_events}."
                        )
                record_ingestion_state(session, provider_name, target_date)
                session.commit()
                num_dates += 1
            log(
                f"Finished NBA play-by-play ingest: dates_processed={num_dates}, "
                f"pbp_events={total_events}."
            )
        except Exception as exc:
            log(f"Error during NBA play-by-play ingest: {exc}")
            notify(f"❌ NBA play-by-play ingest FAILED for provider {provider_name}: {exc}")
            raise
        notify(
            "✅ NBA play-by-play ingest finished for provider "
            f"{provider_name}: dates={num_dates}, events={total_events}."
        )


if __name__ == "__main__":
    main()

"""ETL for syncing teams, games, and stats from Ball Don't Lie into Postgres."""
from __future__ import annotations

import argparse
from datetime import datetime
from typing import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .balldontlie_client import BallDontLieClient
from .config import load_settings
from .db import create_db_engine, create_session_factory, get_session
from .models import (
    Base,
    Game,
    Player,
    PlayerGameAdvanced,
    PlayerGameStat,
    Team,
)
from .normalization import canonicalize_player_name, canonicalize_team_name, season_label


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    cleaned = value
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def _upsert_team(session: Session, payload: dict) -> None:
    canonical = canonicalize_team_name(payload.get("full_name"))
    values = {
        "name": payload.get("full_name"),
        "abbreviation": (payload.get("abbreviation") or "").upper(),
        "city": payload.get("city"),
        "conference": payload.get("conference"),
        "division": payload.get("division"),
        "bdl_team_id": payload.get("id"),
        "canonical_name": canonical,
    }

    stmt = insert(Team).values(**values)

    # Always upsert based on canonical_name so we merge into any existing team row
    # (e.g., one that was created from another provider before Ball Don't Lie).
    stmt = stmt.on_conflict_do_update(
        index_elements=[Team.canonical_name],
        set_={k: values[k] for k in values if k != "canonical_name"},
    )

    session.execute(stmt)



def _team_db_id(session: Session, bdl_team_id: int | None) -> int | None:
    if bdl_team_id is None:
        return None
    return session.execute(select(Team.id).where(Team.bdl_team_id == bdl_team_id)).scalar_one_or_none()


def _ensure_game(session: Session, payload: dict) -> int | None:
    home_team = payload.get("home_team") or {}
    away_team = payload.get("visitor_team") or {}
    home_id = _team_db_id(session, home_team.get("id"))
    away_id = _team_db_id(session, away_team.get("id"))
    if not home_id or not away_id:
        return None
    raw_date = payload.get("date") or ""
    if not raw_date:
        parsed_date = datetime.utcnow().date()
    elif "T" in raw_date:
        try:
            parsed_date = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).date()
        except ValueError:
            parsed_date = datetime.strptime(raw_date.split("T")[0], "%Y-%m-%d").date()
    else:
        parsed_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
    game_values = {
        "game_date": parsed_date,
        "season": season_label(payload.get("season")),
        "season_type": "playoffs" if payload.get("postseason") else "regular",
        "home_team_id": home_id,
        "away_team_id": away_id,
        "home_score": payload.get("home_team_score"),
        "away_score": payload.get("visitor_team_score"),
        "tipoff_datetime_utc": _parse_datetime(payload.get("datetime")),
        "bdl_game_id": payload.get("id"),
    }
    stmt = (
        insert(Game)
        .values(**game_values)
        .on_conflict_do_update(
            constraint="uq_games_bdl_game_id",
            set_={
                "home_score": game_values["home_score"],
                "away_score": game_values["away_score"],
                "tipoff_datetime_utc": game_values["tipoff_datetime_utc"],
            },
        )
        .returning(Game.id)
    )
    return session.execute(stmt).scalar_one()


def _ensure_player(session: Session, payload: dict) -> int:
    full_name = payload.get("full_name") or f"{payload.get('first_name','').strip()} {payload.get('last_name','').strip()}".strip()
    canonical = canonicalize_player_name(full_name)
    values = {
        "full_name": full_name or "unknown",
        "canonical_name": canonical,
        "bdl_player_id": payload.get("id"),
        "position": payload.get("position"),
        "height": payload.get("height"),
        "weight": payload.get("weight"),
    }
    stmt = insert(Player).values(**values)
    conflict_target = "uq_players_bdl_player_id" if payload.get("id") is not None else None
    if conflict_target:
        stmt = stmt.on_conflict_do_update(
            constraint=conflict_target,
            set_={k: values[k] for k in values if k not in {"bdl_player_id", "canonical_name"}},
        )
    else:
        stmt = stmt.on_conflict_do_update(
            index_elements=[Player.canonical_name],
            set_={k: values[k] for k in values if k != "canonical_name"},
        )
    stmt = stmt.returning(Player.id)
    return session.execute(stmt).scalar_one()


def _upsert_player_stat(session: Session, stat: dict) -> None:
    game = stat.get("game", {})
    team = stat.get("team", {})
    player = stat.get("player", {})
    game_id = session.execute(select(Game.id).where(Game.bdl_game_id == game.get("id"))).scalar_one_or_none()
    if not game_id:
        return
    player_payload = {
        "id": player.get("id"),
        "full_name": player.get("full_name"),
        "first_name": player.get("first_name"),
        "last_name": player.get("last_name"),
        "position": player.get("position"),
        "height": player.get("height"),
        "weight": player.get("weight"),
    }
    player_id = _ensure_player(session, player_payload)
    team_id = _team_db_id(session, team.get("id"))
    if not team_id:
        return
    payload = {
        "game_id": game_id,
        "player_id": player_id,
        "team_id": team_id,
        "minutes": stat.get("min"),
        "points": stat.get("pts"),
        "rebounds": stat.get("reb"),
        "assists": stat.get("ast"),
        "blocks": stat.get("blk"),
        "steals": stat.get("stl"),
        "fg_attempts": stat.get("fga"),
        "fg_made": stat.get("fgm"),
        "three_attempts": stat.get("fg3a"),
        "three_made": stat.get("fg3m"),
        "ft_attempts": stat.get("fta"),
        "ft_made": stat.get("ftm"),
        "turnovers": stat.get("turnover"),
        "plus_minus": stat.get("plus_minus"),
        "raw_json": stat,
    }
    stmt = (
        insert(PlayerGameStat)
        .values(**payload)
        .on_conflict_do_update(
            constraint="uq_player_game_stats_game_player",
            set_={k: payload[k] for k in payload if k not in {"game_id", "player_id", "team_id"}},
        )
    )
    session.execute(stmt)


def _upsert_player_advanced(session: Session, stat: dict) -> None:
    game = stat.get("game", {})
    team = stat.get("team", {})
    player = stat.get("player", {})
    game_id = session.execute(select(Game.id).where(Game.bdl_game_id == game.get("id"))).scalar_one_or_none()
    if not game_id:
        return
    player_payload = {
        "id": player.get("id"),
        "full_name": player.get("full_name"),
        "first_name": player.get("first_name"),
        "last_name": player.get("last_name"),
        "position": player.get("position"),
        "height": player.get("height"),
        "weight": player.get("weight"),
    }
    player_id = _ensure_player(session, player_payload)
    team_id = _team_db_id(session, team.get("id"))
    if not team_id:
        return
    payload = {
        "game_id": game_id,
        "player_id": player_id,
        "team_id": team_id,
        "minutes": stat.get("min"),
        "off_rating": stat.get("off_rating"),
        "def_rating": stat.get("def_rating"),
        "usage_pct": stat.get("usg_pct"),
        "ts_pct": stat.get("ts_pct"),
        "offensive_reb_pct": stat.get("oreb_pct"),
        "defensive_reb_pct": stat.get("dreb_pct"),
        "assist_pct": stat.get("ast_pct"),
        "steal_pct": stat.get("stl_pct"),
        "block_pct": stat.get("blk_pct"),
        "raw_json": stat,
    }
    stmt = (
        insert(PlayerGameAdvanced)
        .values(**payload)
        .on_conflict_do_update(
            constraint="uq_player_game_adv_game_player",
            set_={k: payload[k] for k in payload if k not in {"game_id", "player_id", "team_id"}},
        )
    )
    session.execute(stmt)


def ingest_teams(client: BallDontLieClient, session: Session) -> int:
    teams = client.list_teams()
    for team in teams:
        _upsert_team(session, team)
    return len(teams)


def ingest_games(
    client: BallDontLieClient,
    session: Session,
    seasons: Iterable[int],
    postseason: bool | None,
) -> int:
    count = 0
    for game in client.list_games_for_seasons(seasons, postseason=postseason):
        if _ensure_game(session, game):
            count += 1
    return count


def ingest_player_stats(
    client: BallDontLieClient,
    session: Session,
    seasons: Iterable[int],
    postseason: bool | None,
) -> int:
    count = 0
    for stat in client.list_stats_for_seasons(seasons, postseason=postseason):
        _upsert_player_stat(session, stat)
        count += 1
    return count


def ingest_player_advanced(
    client: BallDontLieClient,
    session: Session,
    seasons: Iterable[int],
    postseason: bool | None,
) -> int:
    count = 0
    for stat in client.list_advanced_stats_for_seasons(seasons, postseason=postseason):
        _upsert_player_advanced(session, stat)
        count += 1
    return count


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Backfill Ball Don't Lie data into the warehouse"
    )
    parser.add_argument(
        "--postseason",
        action="store_true",
        help="Only ingest playoff games/stats",
    )
    parser.add_argument(
        "--start",
        type=str,
        help="Start date (YYYY-MM-DD) used to infer NBA seasons",
    )
    parser.add_argument(
        "--end",
        type=str,
        help="End date (YYYY-MM-DD) used to infer NBA seasons",
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    seasons = settings.seasons

    # If --start/--end are provided, override the seasons list
    if args.start or args.end:
        if not (args.start and args.end):
            parser.error("--start and --end must be provided together")

        try:
            start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
        except ValueError:
            parser.error(f"Invalid --start date '{args.start}', expected YYYY-MM-DD")

        try:
            end_date = datetime.strptime(args.end, "%Y-%m-%d").date()
        except ValueError:
            parser.error(f"Invalid --end date '{args.end}', expected YYYY-MM-DD")

        if end_date < start_date:
            parser.error("--end date cannot be earlier than --start date")

        # NBA season is labeled by the year it starts (e.g. 2021-22 -> 2021)
        def season_for_date(d):
            return d.year if d.month >= 7 else d.year - 1

        start_season = season_for_date(start_date)
        end_season = season_for_date(end_date)
        seasons = list(range(start_season, end_season + 1))

    postseason_flag = True if args.postseason else None

    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    client = BallDontLieClient(settings.api_key)

    with get_session(session_factory) as session:
        teams = ingest_teams(client, session)
        games = ingest_games(client, session, seasons, postseason_flag)
        stats = ingest_player_stats(client, session, seasons, postseason_flag)
        advanced = ingest_player_advanced(client, session, seasons, postseason_flag)
        print(
            f"Ingested teams={teams} games={games} box_rows={stats} advanced_rows={advanced}"
        )


if __name__ == "__main__":
    main()

"""ETL for syncing teams, games, and stats from Ball Don't Lie into Postgres."""
from __future__ import annotations

import argparse
import math
from datetime import date, datetime
from typing import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .balldontlie_client import BallDontLieClient
from .config import load_settings
from .db import create_db_engine, create_session_factory, get_session
from .ingestion_status import is_ingestion_complete, mark_ingestion_complete
from .models import (
    Base,
    Game,
    Player,
    PlayerGameAdvanced,
    PlayerGameStat,
    SeasonAverage,
    Team,
)
from .normalization import canonicalize_player_name, canonicalize_team_name, season_label
from .notifications import notify

SEASON_AVERAGE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "general": ("base", "advanced", "usage", "scoring", "defense", "misc"),
    "clutch": ("base", "advanced", "scoring", "usage", "misc"),
    "defense": (
        "overall",
        "less_than_6ft",
        "less_than_10ft",
        "greater_than_15ft",
        "2_pointers",
        "3_pointers",
    ),
    "shooting": ("5ft_range", "by_zone"),
}

SEASON_AVERAGE_SEASON_TYPES: tuple[str, ...] = (
    "regular",
    "playoffs",
    "playin",
    "ist",
)


def log(message: str) -> None:
    print(f"[BDL] {message}", flush=True)


def _to_int(value: object | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_bool(value: object | None) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "t", "1", "yes"}:
            return True
        if lowered in {"false", "f", "0", "no"}:
            return False
    return None


def _nested_value(payload: dict, path: tuple[str, ...]) -> object | None:
    current: object | None = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _score_value(payload: dict, *paths: tuple[str, ...] | str) -> int | None:
    for path in paths:
        if isinstance(path, tuple):
            candidate = _nested_value(payload, path)
        else:
            candidate = payload.get(path)
        value = _to_int(candidate)
        if value is not None:
            return value
    return None


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


def _clean_advanced_value(value: object | None, *, max_abs: float) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric) or math.isinf(numeric):
        return None
    if abs(numeric) > max_abs:
        return None
    return numeric


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
    scoreboard_fields = {
        "status": payload.get("status"),
        "period": _to_int(payload.get("period")),
        "time": payload.get("time"),
        "postseason": _to_bool(payload.get("postseason")),
        "home_q1": _score_value(
            payload,
            "home_q1",
            ("home_period_scores", "q1"),
            ("scores", "home", "q1"),
        ),
        "home_q2": _score_value(
            payload,
            "home_q2",
            ("home_period_scores", "q2"),
            ("scores", "home", "q2"),
        ),
        "home_q3": _score_value(
            payload,
            "home_q3",
            ("home_period_scores", "q3"),
            ("scores", "home", "q3"),
        ),
        "home_q4": _score_value(
            payload,
            "home_q4",
            ("home_period_scores", "q4"),
            ("scores", "home", "q4"),
        ),
        "home_ot": _score_value(
            payload,
            "home_ot",
            ("home_period_scores", "ot"),
            ("scores", "home", "ot"),
        ),
        "away_q1": _score_value(
            payload,
            "away_q1",
            ("visitor_period_scores", "q1"),
            ("scores", "visitor", "q1"),
        ),
        "away_q2": _score_value(
            payload,
            "away_q2",
            ("visitor_period_scores", "q2"),
            ("scores", "visitor", "q2"),
        ),
        "away_q3": _score_value(
            payload,
            "away_q3",
            ("visitor_period_scores", "q3"),
            ("scores", "visitor", "q3"),
        ),
        "away_q4": _score_value(
            payload,
            "away_q4",
            ("visitor_period_scores", "q4"),
            ("scores", "visitor", "q4"),
        ),
        "away_ot": _score_value(
            payload,
            "away_ot",
            ("visitor_period_scores", "ot"),
            ("scores", "visitor", "ot"),
        ),
        "home_timeouts_remaining": _to_int(payload.get("home_timeouts_remaining")),
        "away_timeouts_remaining": _to_int(payload.get("away_timeouts_remaining")),
        "home_in_bonus": _to_bool(payload.get("home_in_bonus")),
        "away_in_bonus": _to_bool(payload.get("away_in_bonus")),
    }

    game_values = {
        "game_date": parsed_date,
        "season": season_label(payload.get("season")),
        "season_type": payload.get("season_type")
        or ("playoffs" if payload.get("postseason") else "regular"),
        "home_team_id": home_id,
        "away_team_id": away_id,
        "home_score": payload.get("home_team_score"),
        "away_score": payload.get("visitor_team_score"),
        "tipoff_datetime_utc": _parse_datetime(payload.get("datetime")),
        "bdl_game_id": payload.get("id"),
        "raw_json": payload,
    }
    game_values.update(scoreboard_fields)
    stmt = (
        insert(Game)
        .values(**game_values)
        .on_conflict_do_update(
            constraint="uq_games_bdl_game_id",
            set_={
                "home_score": game_values["home_score"],
                "away_score": game_values["away_score"],
                "tipoff_datetime_utc": game_values["tipoff_datetime_utc"],
                "status": game_values["status"],
                "period": game_values["period"],
                "time": game_values["time"],
                "postseason": game_values["postseason"],
                "home_q1": game_values["home_q1"],
                "home_q2": game_values["home_q2"],
                "home_q3": game_values["home_q3"],
                "home_q4": game_values["home_q4"],
                "home_ot": game_values["home_ot"],
                "away_q1": game_values["away_q1"],
                "away_q2": game_values["away_q2"],
                "away_q3": game_values["away_q3"],
                "away_q4": game_values["away_q4"],
                "away_ot": game_values["away_ot"],
                "home_timeouts_remaining": game_values["home_timeouts_remaining"],
                "away_timeouts_remaining": game_values["away_timeouts_remaining"],
                "home_in_bonus": game_values["home_in_bonus"],
                "away_in_bonus": game_values["away_in_bonus"],
                "raw_json": game_values["raw_json"],
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
    advanced_metrics = {
        "off_rating": _clean_advanced_value(stat.get("off_rating"), max_abs=1000),
        "def_rating": _clean_advanced_value(stat.get("def_rating"), max_abs=1000),
        "usage_pct": _clean_advanced_value(stat.get("usg_pct"), max_abs=100),
        "ts_pct": _clean_advanced_value(stat.get("ts_pct"), max_abs=100),
        "offensive_reb_pct": _clean_advanced_value(stat.get("oreb_pct"), max_abs=100),
        "defensive_reb_pct": _clean_advanced_value(stat.get("dreb_pct"), max_abs=100),
        "assist_pct": _clean_advanced_value(stat.get("ast_pct"), max_abs=100),
        "steal_pct": _clean_advanced_value(stat.get("stl_pct"), max_abs=100),
        "block_pct": _clean_advanced_value(stat.get("blk_pct"), max_abs=100),
        "pace": _clean_advanced_value(stat.get("pace"), max_abs=1000),
        "assist_ratio": _clean_advanced_value(stat.get("ast_ratio"), max_abs=1000),
        "assist_to_turnover": _clean_advanced_value(stat.get("ast_tov"), max_abs=1000),
        "effective_fg_pct": _clean_advanced_value(stat.get("efg_pct"), max_abs=100),
        "net_rating": _clean_advanced_value(stat.get("net_rating"), max_abs=1000),
        "rebound_pct": _clean_advanced_value(stat.get("reb_pct"), max_abs=100),
        "turnover_ratio": _clean_advanced_value(stat.get("tov_ratio"), max_abs=1000),
    }
    payload = {
        "game_id": game_id,
        "player_id": player_id,
        "team_id": team_id,
        "minutes": stat.get("min"),
        "pie": stat.get("pie"),
        "off_rating": _clean_advanced_value(stat.get("off_rating"), max_abs=1000),
        "def_rating": _clean_advanced_value(stat.get("def_rating"), max_abs=1000),
        "usage_pct": _clean_advanced_value(stat.get("usg_pct"), max_abs=100),
        "ts_pct": _clean_advanced_value(stat.get("ts_pct"), max_abs=100),
        "offensive_reb_pct": _clean_advanced_value(stat.get("oreb_pct"), max_abs=100),
        "defensive_reb_pct": _clean_advanced_value(stat.get("dreb_pct"), max_abs=100),
        "assist_pct": _clean_advanced_value(stat.get("ast_pct"), max_abs=100),
        "steal_pct": _clean_advanced_value(stat.get("stl_pct"), max_abs=100),
        "block_pct": _clean_advanced_value(stat.get("blk_pct"), max_abs=100),
        "pie": stat.get("pie"),
        "pace": _clean_advanced_value(stat.get("pace"), max_abs=1000),
        "assist_ratio": _clean_advanced_value(stat.get("ast_ratio"), max_abs=1000),
        "assist_to_turnover": _clean_advanced_value(stat.get("ast_tov"), max_abs=1000),
        "effective_fg_pct": _clean_advanced_value(stat.get("efg_pct"), max_abs=100),
        "net_rating": _clean_advanced_value(stat.get("net_rating"), max_abs=1000),
        "rebound_pct": _clean_advanced_value(stat.get("reb_pct"), max_abs=100),
        "turnover_ratio": _clean_advanced_value(stat.get("tov_ratio"), max_abs=1000),
        "off_rating": stat.get("off_rating"),
        "def_rating": stat.get("def_rating"),
        "usage_pct": stat.get("usg_pct"),
        "ts_pct": stat.get("ts_pct"),
        "offensive_reb_pct": stat.get("oreb_pct"),
        "defensive_reb_pct": stat.get("dreb_pct"),
        "assist_pct": stat.get("ast_pct"),
        "steal_pct": stat.get("stl_pct"),
        "block_pct": stat.get("blk_pct"),
        "pie": stat.get("pie"),
        "pace": stat.get("pace"),
        "assist_ratio": stat.get("ast_ratio"),
        "assist_to_turnover": stat.get("ast_tov"),
        "effective_fg_pct": stat.get("efg_pct"),
        "net_rating": stat.get("net_rating"),
        "rebound_pct": stat.get("reb_pct"),
        "turnover_ratio": stat.get("tov_ratio"),
        "raw_json": stat,
        **advanced_metrics,
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


def _upsert_season_average(
    session: Session,
    payload: dict,
    *,
    season: int,
    season_type: str,
    category: str,
    stat_type: str,
) -> None:
    player = payload.get("player", {}) or {}
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
    stats = payload.get("stats") or {}
    record = {
        "player_id": player_id,
        "season": season,
        "season_type": season_type,
        "category": category,
        "stat_type": stat_type,
        "stats": stats,
        "raw_player": player or None,
    }
    stmt = (
        insert(SeasonAverage)
        .values(**record)
        .on_conflict_do_update(
            constraint="uq_season_avg_player_season_category",
            set_={
                "stats": record["stats"],
                "raw_player": record["raw_player"],
            },
        )
    )
    session.execute(stmt)


def ingest_teams(client: BallDontLieClient, session: Session) -> list[dict]:
    log("Fetching team directory from BallDontLie...")
    teams = client.list_teams()
    for team in teams:
        _upsert_team(session, team)
    log(f"Ingested/updated {len(teams)} teams.")
    return teams


def _allowed_team_ids(requested: str | None, teams: list[dict]) -> set[int] | None:
    if not requested:
        return None

    allowed: set[int] = set()
    requested_abbrevs = {
        item.strip().upper()
        for token in requested.split(",")
        for item in [token]
        if item.strip()
    }
    requested_ids = {int(value) for value in requested_abbrevs if value.isdigit()}
    requested_canonical = {
        canonicalize_team_name(value) for value in requested_abbrevs if not value.isdigit()
    }

    for team in teams:
        team_id = _to_int(team.get("id"))
        if team_id is None:
            continue
        abbreviation = (team.get("abbreviation") or "").upper()
        canonical = canonicalize_team_name(team.get("full_name"))

        if team_id in requested_ids:
            allowed.add(team_id)
            continue
        if abbreviation and abbreviation in requested_abbrevs:
            allowed.add(team_id)
            continue
        if canonical in requested_canonical:
            allowed.add(team_id)

    if not allowed:
        log(
            "No teams matched requested filters %s; proceeding without team filter."
            % sorted(requested_abbrevs)
        )
        return None

    log(
        "Limiting ingestion to teams: %s"
        % ", ".join(sorted(str(team_id) for team_id in allowed))
    )
    return allowed


def ingest_games(
    client: BallDontLieClient,
    session: Session,
    seasons: Iterable[int],
    postseason: bool | None,
    *,
    allowed_team_ids: set[int] | None = None,
) -> int:
    count = 0
    for season in list(seasons):
        log(f"Processing season {season} (postseason={postseason}) for games...")
        season_count = 0
        for game in client.list_games_for_seasons([season], postseason=postseason):
            home_team = (game.get("home_team") or {}).get("id")
            away_team = (game.get("visitor_team") or {}).get("id")
            if allowed_team_ids and not {
                _to_int(home_team),
                _to_int(away_team),
            } & allowed_team_ids:
                continue
            if _ensure_game(session, game):
                season_count += 1
        log(f"Season {season}: ingested {season_count} games.")
        count += season_count
    return count


def ingest_player_stats(
    client: BallDontLieClient,
    session: Session,
    seasons: Iterable[int],
    postseason: bool | None,
    *,
    allowed_team_ids: set[int] | None = None,
) -> int:
    count = 0
    for season in list(seasons):
        log(f"Processing season {season} (postseason={postseason}) for box scores...")
        season_count = 0
        for stat in client.list_stats_for_seasons([season], postseason=postseason):
            team_id = _to_int((stat.get("team") or {}).get("id"))
            game = stat.get("game") or {}
            home_team_id = _to_int((game.get("home_team") or {}).get("id"))
            away_team_id = _to_int((game.get("visitor_team") or {}).get("id"))
            if allowed_team_ids and not {
                team_id,
                home_team_id,
                away_team_id,
            } & allowed_team_ids:
                continue
            _upsert_player_stat(session, stat)
            season_count += 1
            if season_count % 500 == 0:
                log(f"Season {season}: processed {season_count} box score rows so far...")
        log(f"Season {season}: ingested {season_count} player box score rows.")
        count += season_count
    return count


def ingest_player_advanced(
    client: BallDontLieClient,
    session: Session,
    seasons: Iterable[int],
    postseason: bool | None,
    *,
    allowed_team_ids: set[int] | None = None,
) -> int:
    count = 0
    for season in list(seasons):
        log(f"Processing season {season} (postseason={postseason}) for advanced stats...")
        season_count = 0
        for stat in client.list_advanced_stats_for_seasons([season], postseason=postseason):
            team_id = _to_int((stat.get("team") or {}).get("id"))
            game = stat.get("game") or {}
            home_team_id = _to_int((game.get("home_team") or {}).get("id"))
            away_team_id = _to_int((game.get("visitor_team") or {}).get("id"))
            if allowed_team_ids and not {
                team_id,
                home_team_id,
                away_team_id,
            } & allowed_team_ids:
                continue
            _upsert_player_advanced(session, stat)
            season_count += 1
            if season_count % 500 == 0:
                log(f"Season {season}: processed {season_count} advanced stat rows so far...")
        log(f"Season {season}: ingested {season_count} advanced stat rows.")
        count += season_count
    return count


def ingest_season_averages(
    client: BallDontLieClient,
    session: Session,
    seasons: Iterable[int],
    season_types: Sequence[str] | None = None,
    *,
    allowed_team_ids: set[int] | None = None,
) -> int:
    total = 0
    seasons = list(seasons)
    if season_types is None:
        season_types = list(SEASON_AVERAGE_SEASON_TYPES)
    else:
        season_types = list(season_types)

    for season in seasons:
        for season_type in season_types:
            log(
                "Processing season %s (%s) for season averages..."
                % (season, season_type)
            )
            for category, stat_types in SEASON_AVERAGE_CATEGORIES.items():
                for stat_type in stat_types:
                    for payload in client.list_season_averages(
                        season,
                        season_type=season_type,
                        category=category,
                        stat_type=stat_type,
                    ):
                        team_id = _to_int((payload.get("team") or {}).get("id"))
                        if (
                            allowed_team_ids
                            and team_id is not None
                            and team_id not in allowed_team_ids
                        ):
                            continue
                        _upsert_season_average(
                            session,
                            payload,
                            season=season,
                            season_type=season_type,
                            category=category,
                            stat_type=stat_type,
                        )
                        total += 1
    log(
        "Finished ingesting %s season average rows for seasons=%s season_types=%s"
        % (total, seasons, season_types)
    )
    return total


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
    parser.add_argument(
        "--teams",
        type=str,
        help=(
            "Comma separated team abbreviations or IDs to limit ingestion (e.g., "
            "'DEN,GSW')."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore ingestion_status checks and re-run seasons regardless of prior runs",
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    seasons = list(settings.seasons)

    # If --start/--end are provided, override the seasons list
    start_bound: date | None = None
    end_bound: date | None = None
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
        start_bound = start_date
        end_bound = end_date

    postseason_flag = True if args.postseason else None

    if start_bound and end_bound:
        log(
            f"Starting BallDontLie ingest from {start_bound} to {end_bound} "
            f"(seasons={seasons}, postseason={postseason_flag})."
        )
    else:
        log(
            f"Starting BallDontLie ingest for seasons={seasons} "
            f"postseason={postseason_flag}."
        )

    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    client = BallDontLieClient(settings.api_key)
    min_season = min(seasons) if seasons else None
    max_season = max(seasons) if seasons else None

    with get_session(session_factory) as session:
        teams = ingest_teams(client, session)
        allowed_team_ids = _allowed_team_ids(args.teams, teams)
        total_games = 0
        total_stats = 0
        total_advanced = 0
        total_season_avgs = 0
        for season in seasons:
            if not args.force and is_ingestion_complete(
                session, "balldontlie", season, "stats"
            ):
                log(
                    f"Season {season} already ingested for balldontlie stats, skipping."
                )
                notify(f"⏭️ Season {season} already complete, skipping")
                continue
            log(f"Starting balldontlie ingestion for season {season}...")
            notify(f"🏀 Starting ingestion for season {season}...")
            try:
                games = ingest_games(
                    client,
                    session,
                    [season],
                    postseason_flag,
                    allowed_team_ids=allowed_team_ids,
                )
                stats = ingest_player_stats(
                    client,
                    session,
                    [season],
                    postseason_flag,
                    allowed_team_ids=allowed_team_ids,
                )
                advanced = ingest_player_advanced(
                    client,
                    session,
                    [season],
                    postseason_flag,
                    allowed_team_ids=allowed_team_ids,
                )
                season_avgs = ingest_season_averages(
                    client,
                    session,
                    [season],
                    allowed_team_ids=allowed_team_ids,
                )
                session.commit()
                if allowed_team_ids:
                    log(
                        "Team filter in use; not marking season %s as fully ingested.",
                        season,
                    )
                else:
                    mark_ingestion_complete(session, "balldontlie", season, "stats")
            except Exception as exc:
                session.rollback()
                log(f"Error ingesting season {season}: {exc}")
                notify(f"❌ balldontlie ingest FAILED for season {season}: {exc}")
                raise
            total_games += games
            total_stats += stats
            total_advanced += advanced
            total_season_avgs += season_avgs
            log(
                f"Completed balldontlie season {season}: games={games}, box_rows={stats}, "
                f"advanced_rows={advanced}, season_average_rows={season_avgs}."
            )
            notify(
                f"✅ Season {season} complete: {games} games, {stats} stats, "
                f"{advanced} advanced stats ingested"
            )
        log(
            f"Finished BallDontLie ingest: teams={len(teams)}, games={total_games}, "
            f"box_rows={total_stats}, advanced_rows={total_advanced}, "
            f"season_average_rows={total_season_avgs}."
        )
        if min_season is not None and max_season is not None:
            notify(
                "✅ balldontlie ingest finished successfully for seasons "
                f"{min_season}–{max_season}."
            )


if __name__ == "__main__":
    main()

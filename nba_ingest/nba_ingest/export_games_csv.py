"""Export a week's worth of games from each provider to CSV for ID comparison."""
from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence

from sqlalchemy import and_, select
from sqlalchemy.orm import aliased

from .balldontlie_client import BallDontLieClient
from .db import create_db_engine, create_session_factory, get_session
from .models import Game, Team
from .unified_odds_ingest import BetsApiClient, SportsGameOddsClient, TheOddsApiClient

CSV_COLUMNS: list[str] = [
    "provider",
    "source_event_id",
    "internal_game_id",
    "game_date",
    "start_time",
    "season",
    "status",
    "home_team",
    "home_abbr",
    "home_provider_id",
    "home_provider_ids",
    "away_team",
    "away_abbr",
    "away_provider_id",
    "away_provider_ids",
    "extra",
]


def _require_env(var_name: str, default: str | None = None) -> str:
    value = os.environ.get(var_name, default)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {var_name}")
    return value


def _balldontlie_api_key(cli_value: str | None = None, *, required: bool = False) -> str | None:
    """Retrieve a Ball Don't Lie API key from CLI args or either env var spelling.

    Accepts both ``BALDONTLIE_API_KEY`` (existing project default) and
    ``BALLDONTLIE_API_KEY`` to avoid typos. Raises a descriptive error when
    ``required`` is ``True`` and no key is available.
    """

    if cli_value:
        return cli_value

    for env_name in ("BALDONTLIE_API_KEY", "BALLDONTLIE_API_KEY"):
        api_key = os.environ.get(env_name)
        if api_key:
            return api_key

    if required:
        raise RuntimeError(
            "Missing Ball Don't Lie API key. Set BALDONTLIE_API_KEY (or BALLDONTLIE_API_KEY) "
            "or pass --balldontlie-api-key."
        )

    return None


def _parse_date(raw: str) -> date:
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:  # pragma: no cover - CLI validation
        raise argparse.ArgumentTypeError(f"Invalid date '{raw}', expected YYYY-MM-DD") from exc


def _date_range(start: date, days: int) -> Iterable[date]:
    for offset in range(days):
        yield start + timedelta(days=offset)


def _json(value: object | None) -> str:
    if value in (None, {}):
        return ""
    return json.dumps(value, sort_keys=True)


def _team_name(raw: object) -> str:
    if raw is None:
        return ""
    if isinstance(raw, dict):
        return (
            raw.get("full_name")
            or raw.get("name")
            or raw.get("team")
            or raw.get("title")
            or raw.get("key")
            or ""
        )
    return str(raw)


def _write_csv(rows: list[dict[str, object]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def _export_balldontlie(
    start: date,
    end: date,
    *,
    output: Path,
    api_key: str | None = None,
) -> Path:
    resolved_api_key = _balldontlie_api_key(api_key, required=True)
    client = BallDontLieClient(resolved_api_key)

    start: date, end: date, *, output: Path, api_key: str | None = None
) -> Path:
    client = BallDontLieClient(_balldontlie_api_key(api_key, required=True))
def _export_balldontlie(start: date, end: date, *, output: Path) -> Path:
    api_key = _require_env("BALLDONTLIE_API_KEY")
    client = BallDontLieClient(api_key)
    games = client.list_games_by_date_range(start, end)
    rows: list[dict[str, object]] = []
    for game in games:
        home = game.get("home_team") or {}
        away = game.get("visitor_team") or {}
        rows.append(
            {
                "provider": "balldontlie",
                "source_event_id": game.get("id"),
                "internal_game_id": "",
                "game_date": (game.get("date") or "")[:10],
                "start_time": game.get("date"),
                "season": game.get("season"),
                "status": game.get("status"),
                "home_team": home.get("full_name") or home.get("name"),
                "home_abbr": (home.get("abbreviation") or "").upper(),
                "home_provider_id": home.get("id"),
                "home_provider_ids": "",
                "away_team": away.get("full_name") or away.get("name"),
                "away_abbr": (away.get("abbreviation") or "").upper(),
                "away_provider_id": away.get("id"),
                "away_provider_ids": "",
                "extra": _json(
                    {
                        "period": game.get("period"),
                        "postseason": game.get("postseason"),
                    }
                ),
                    {"period": game.get("period"), "postseason": game.get("postseason")}
                ),
                "extra": _json({"period": game.get("period"), "postseason": game.get("postseason")}),
            }
        )
    return _write_csv(rows, output)


def _export_odds_api(start: date, days: int, *, output: Path) -> Path:
    api_key = _require_env("ODDS_API_KEY")
    base_url = os.environ.get("ODDS_API_BASE_URL", "https://api.the-odds-api.com/v4")
    client = TheOddsApiClient(api_key, base_url)
    rows: list[dict[str, object]] = []
    for target_date in _date_range(start, days):
        for event in client.list_events_for_date(target_date):
            home = event.get("home_team") or event.get("homeTeam") or {}
            away = event.get("away_team") or event.get("awayTeam") or {}
            rows.append(
                {
                    "provider": "odds_api",
                    "source_event_id": event.get("id"),
                    "internal_game_id": "",
                    "game_date": target_date.isoformat(),
                    "start_time": event.get("commence_time") or event.get("commenceTime"),
                    "season": "",
                    "status": event.get("completed") or event.get("status"),
                    "home_team": _team_name(home),
                    "home_abbr": "",
                    "home_provider_id": home.get("id") if isinstance(home, dict) else "",
                    "home_provider_ids": "",
                    "away_team": _team_name(away),
                    "away_abbr": "",
                    "away_provider_id": away.get("id") if isinstance(away, dict) else "",
                    "away_provider_ids": "",
                    "extra": _json({"sport_key": event.get("sport_key") or event.get("sportKey")}),
                }
            )
    return _write_csv(rows, output)


def _export_sgo(start: date, days: int, *, output: Path) -> Path:
    api_key = _require_env("SPORTSGAMEODDS_API_KEY")
    base_url = os.environ.get("SGO_BASE_URL", "https://api.sportsgameodds.com/v2")
    client = SportsGameOddsClient(api_key, base_url)
    rows: list[dict[str, object]] = []
    for target_date in _date_range(start, days):
        for event in client.fetch_events_for_date(target_date):
            home = event.get("homeTeam") or event.get("home") or {}
            away = event.get("awayTeam") or event.get("away") or {}
            start_time = event.get("startTime") or event.get("startsAt") or event.get("commenceTime")
            home_abbr = ""
            away_abbr = ""
            if isinstance(home, dict):
                home_abbr = home.get("abbrev") or home.get("abbreviation") or ""
            if isinstance(away, dict):
                away_abbr = away.get("abbrev") or away.get("abbreviation") or ""
            rows.append(
                {
                    "provider": "sgo",
                    "source_event_id": event.get("id"),
                    "internal_game_id": "",
                    "game_date": target_date.isoformat(),
                    "start_time": start_time,
                    "season": "",
                    "status": event.get("status"),
                    "home_team": _team_name(home),
                    "home_abbr": home_abbr,
                    "home_provider_id": home.get("id") if isinstance(home, dict) else "",
                    "home_provider_ids": "",
                    "away_team": _team_name(away),
                    "away_abbr": away_abbr,
                    "away_provider_id": away.get("id") if isinstance(away, dict) else "",
                    "away_provider_ids": "",
                    "extra": _json({"league": (event.get("league") or {}).get("name"), "bookmakers": len(event.get("bookmakers") or [])}),
                }
            )
    return _write_csv(rows, output)


def _export_betsapi(start: date, days: int, *, output: Path) -> Path:
    api_key = _require_env("BETSAPI_API_KEY")
    base_url = os.environ.get("BETSAPI_BASE_URL", "https://api.betsapi.com/v1")
    client = BetsApiClient(api_key, base_url)
    rows: list[dict[str, object]] = []
    for target_date in _date_range(start, days):
        for event in client.list_events_for_date(target_date):
            home = event.get("home") or event.get("homeTeam") or event.get("home_name")
            away = event.get("away") or event.get("awayTeam") or event.get("away_name")
            rows.append(
                {
                    "provider": "betsapi",
                    "source_event_id": event.get("id") or event.get("event_id"),
                    "internal_game_id": "",
                    "game_date": target_date.isoformat(),
                    "start_time": event.get("time") or event.get("start_time"),
                    "season": "",
                    "status": event.get("status"),
                    "home_team": _team_name(home),
                    "home_abbr": "",
                    "home_provider_id": "",
                    "home_provider_ids": "",
                    "away_team": _team_name(away),
                    "away_abbr": "",
                    "away_provider_id": "",
                    "away_provider_ids": "",
                    "extra": _json({"league": event.get("league"), "timer": event.get("timer")}),
                }
            )
    return _write_csv(rows, output)


def _export_unified(start: date, days: int, *, output: Path) -> Path:
    database_url = _require_env("DATABASE_URL")
    engine = create_db_engine(database_url)
    session_factory = create_session_factory(engine)

    rows: list[dict[str, object]] = []
    home_alias = aliased(Team)
    away_alias = aliased(Team)
    end_date = start + timedelta(days=days - 1)
    stmt = (
        select(Game, home_alias, away_alias)
        .join(home_alias, Game.home_team)
        .join(away_alias, Game.away_team)
        .where(and_(Game.game_date >= start, Game.game_date <= end_date))
        .order_by(Game.game_date, Game.id)
    )

    with get_session(session_factory) as session:
        for game, home, away in session.execute(stmt):
            rows.append(
                {
                    "provider": "unified_odds",
                    "source_event_id": _json(game.provider_event_ids)
                    or game.bdl_game_id
                    or game.nba_game_id,
                    "internal_game_id": game.id,
                    "game_date": game.game_date.isoformat(),
                    "start_time": game.start_time_utc.isoformat() if game.start_time_utc else "",
                    "season": game.season,
                    "status": game.status,
                    "home_team": home.name,
                    "home_abbr": home.abbreviation,
                    "home_provider_id": home.bdl_team_id,
                    "home_provider_ids": _json(home.provider_team_ids),
                    "away_team": away.name,
                    "away_abbr": away.abbreviation,
                    "away_provider_id": away.bdl_team_id,
                    "away_provider_ids": _json(away.provider_team_ids),
                    "extra": _json(
                        {
                            "bdl_game_id": game.bdl_game_id,
                            "odds_api_event_id": game.odds_api_event_id,
                            "sgo_event_id": game.sgo_event_id,
                            "provider_event_ids": game.provider_event_ids,
                        }
                    ),
                }
            )
    return _write_csv(rows, output)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Export provider games for a week to CSV to compare team/event IDs"
    )
    parser.add_argument(
        "--provider",
        choices=[
            "balldontlie",
            "odds_api",
            "sgo",
            "betsapi",
            "unified_odds",
            "all",
        ],
        default="all",
        help="Which provider to export (or 'all' to dump one CSV per provider)",
    )
    parser.add_argument("--start", required=True, type=_parse_date, help="Week start date (YYYY-MM-DD)")
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to export (default 7 for a full week)",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output CSV path (only valid when a single provider is selected)",
    )
    parser.add_argument(
        "--balldontlie-api-key",
        dest="balldontlie_api_key",
        help=(
            "Optional Ball Don't Lie API key override (otherwise uses BALDONTLIE_API_KEY or "
            "BALLDONTLIE_API_KEY)"
        ),
    )
    args = parser.parse_args(argv)

    providers = (
        [args.provider]
        if args.provider != "all"
        else ["balldontlie", "odds_api", "sgo", "betsapi", "unified_odds"]
    )

    if args.output and len(providers) != 1:
        parser.error("--output can only be used when selecting a single provider")

    outputs: list[Path] = []
    end_date = args.start + timedelta(days=args.days - 1)
    for provider in providers:
        default_path = Path("reports") / f"{provider}_games_{args.start.isoformat()}_{end_date.isoformat()}.csv"
        path = Path(args.output) if args.output else default_path
        if provider == "balldontlie":
            outputs.append(
                _export_balldontlie(
                    args.start,
                    end_date,
                    output=path,
                    api_key=args.balldontlie_api_key,
                )
            )
                    args.start, end_date, output=path, api_key=args.balldontlie_api_key
                )
            )
            outputs.append(_export_balldontlie(args.start, end_date, output=path))
        elif provider == "odds_api":
            outputs.append(_export_odds_api(args.start, args.days, output=path))
        elif provider == "sgo":
            outputs.append(_export_sgo(args.start, args.days, output=path))
        elif provider == "betsapi":
            outputs.append(_export_betsapi(args.start, args.days, output=path))
        elif provider == "unified_odds":
            outputs.append(_export_unified(args.start, args.days, output=path))

    for output_path in outputs:
        print(f"Wrote {output_path} ({output_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()

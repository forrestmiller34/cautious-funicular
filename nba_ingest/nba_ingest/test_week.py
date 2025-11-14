"""Utility script to run targeted weekly ingestion smoke tests."""
from __future__ import annotations

import argparse
from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .models import Base
from .odds_helpers import get_or_create_league
from .unified_odds_ingest import (
    BetsApiClient,
    IngestionStats,
    Settings,
    SportsGameOddsClient,
    TheOddsApiClient,
    ingest_betsapi_team_odds,
    ingest_odds_api_player_props,
    ingest_sgo_player_props,
    summarize_stats,
)

WEEK_ONE_START = date(2022, 1, 10)
WEEK_TWO_START = date(2023, 10, 16)


def _week_range(start: date) -> tuple[date, date]:
    return start, start + timedelta(days=6)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run curated ingestion weeks for smoke tests")
    parser.add_argument(
        "--no-dry-run",
        action="store_false",
        dest="dry_run",
        help="Apply writes to the configured database (default: dry-run)",
    )
    parser.set_defaults(dry_run=True)
    return parser


def _prepare_session(settings: Settings, dry_run: bool) -> tuple[sessionmaker, int]:
    if dry_run:
        return sessionmaker(), 0
    engine = create_engine(settings.database_url, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    with SessionLocal() as session:
        league = get_or_create_league(session, "NBA")
        session.commit()
        return SessionLocal, league.id


def _print_summary(label: str, stats: list[IngestionStats]) -> None:
    print(f"=== {label} ===")
    for stat in stats:
        print("  ", summarize_stats(stat))


def main() -> None:
    args = _build_parser().parse_args()
    settings = Settings.from_env()
    session_factory, league_id = _prepare_session(settings, args.dry_run)

    bets_client = BetsApiClient(settings.betsapi_api_key, settings.betsapi_base_url)
    sgo_client = SportsGameOddsClient(settings.sportsgameodds_api_key, settings.sportsgameodds_base_url)
    odds_client = TheOddsApiClient(settings.odds_api_key, settings.odds_api_base_url)

    week_one_start, week_one_end = _week_range(WEEK_ONE_START)
    week_one_stats = [
        ingest_betsapi_team_odds(
            session_factory=session_factory,
            league_id=league_id,
            client=bets_client,
            start_date=week_one_start,
            end_date=week_one_end,
            dry_run=args.dry_run,
        ),
        ingest_sgo_player_props(
            session_factory=session_factory,
            league_id=league_id,
            client=sgo_client,
            start_date=week_one_start,
            end_date=week_one_end,
            markets=settings.markets,
            dry_run=args.dry_run,
        ),
    ]
    _print_summary("Week one: BetsAPI + SGO", week_one_stats)

    week_two_start, week_two_end = _week_range(WEEK_TWO_START)
    week_two_stats = [
        ingest_betsapi_team_odds(
            session_factory=session_factory,
            league_id=league_id,
            client=bets_client,
            start_date=week_two_start,
            end_date=week_two_end,
            dry_run=args.dry_run,
        ),
        ingest_odds_api_player_props(
            session_factory=session_factory,
            league_id=league_id,
            client=odds_client,
            start_date=week_two_start,
            end_date=week_two_end,
            markets=settings.markets,
            dry_run=args.dry_run,
        ),
    ]
    _print_summary("Week two: BetsAPI + OddsAPI", week_two_stats)


if __name__ == "__main__":  # pragma: no cover
    main()

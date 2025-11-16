"""Orchestrator for unified odds ingestion and job creation."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import logging
import os
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import create_db_engine, create_session_factory, get_session
from .models import Game, OddsIngestJob
from .odds_usage import record_provider_usage_snapshot
from .sports_game_odds_client import SportsGameOddsClient
from .odds_api_client import TheOddsApiClient
from .unified_odds_ingest import BetsApiClient
from .unified_odds_providers import (
    ProviderUsageContext,
    ingest_betsapi_odds_for_game,
    ingest_oddsapi_odds_for_game,
    ingest_sgo_odds_for_game,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="[UNIFIED_ORCH] %(message)s")

PROVIDERS = ("sgo", "oddsapi", "betsapi")


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _load_clients() -> dict[str, object]:
    clients: dict[str, object] = {}
    sgo_key = os.environ.get("SGO_API_KEY")
    if sgo_key:
        base_url = os.environ.get("SGO_BASE_URL", "https://api.sportsgameodds.com")
        clients["sgo"] = SportsGameOddsClient(sgo_key, base_url)
    odds_key = os.environ.get("ODDS_API_KEY")
    if odds_key:
        base_url = os.environ.get("ODDS_API_BASE_URL", "https://api.the-odds-api.com/v4")
        clients["oddsapi"] = TheOddsApiClient(odds_key, base_url)
    bets_key = os.environ.get("BETSAPI_API_KEY")
    if bets_key:
        base_url = os.environ.get("BETSAPI_BASE_URL", "https://api.betsapi.com/v1")
        clients["betsapi"] = BetsApiClient(bets_key, base_url)
    return clients


def _handler_for_provider(provider: str):
    if provider == "sgo":
        return ingest_sgo_odds_for_game, "sgo_ingested_at"
    if provider == "oddsapi":
        return ingest_oddsapi_odds_for_game, "oddsapi_ingested_at"
    if provider == "betsapi":
        return ingest_betsapi_odds_for_game, "betsapi_ingested_at"
    raise ValueError(f"Unknown provider: {provider}")


def _load_games(session: Session, start: date, end: date) -> list[Game]:
    return (
        session.execute(
            select(Game).where(Game.game_date >= start).where(Game.game_date <= end)
        )
        .scalars()
        .all()
    )


def create_jobs(session: Session, games: list[Game], providers: Iterable[str]) -> tuple[int, int]:
    created = 0
    skipped = 0
    for game in games:
        for provider in providers:
            existing = session.execute(
                select(OddsIngestJob).where(
                    OddsIngestJob.game_id == game.id,
                    OddsIngestJob.provider == provider,
                )
            ).scalar_one_or_none()
            if existing:
                skipped += 1
                continue
            session.add(
                OddsIngestJob(
                    game_id=game.id,
                    provider=provider,
                    status="pending",
                    attempts=0,
                )
            )
            created += 1
    return created, skipped


def _process_game_direct(
    session: Session,
    game: Game,
    provider: str,
    handler,
    timestamp_attr: str,
    client,
    usage: ProviderUsageContext,
) -> int:
    if getattr(game, timestamp_attr):
        logger.info(
            "%s already ingested for game %s on %s", provider, game.id, game.game_date
        )
        return 0
    count = handler(session, game, client=client, usage=usage)
    setattr(game, timestamp_attr, datetime.now(timezone.utc))
    logger.info(
        "%s ingested %s odds rows for game %s", provider, count, game.id
    )
    return count


def run_direct_mode(
    games: list[Game],
    providers: Iterable[str],
    *,
    session_factory,
    clients: dict[str, object],
    usage_trackers: dict[str, ProviderUsageContext],
    periodic_every: int | None,
    window_label: str,
) -> None:
    processed = 0
    for game in games:
        with get_session(session_factory) as session:
            for provider in providers:
                if provider not in clients:
                    logger.warning("Missing client for provider %s; skipping", provider)
                    continue
                handler, ts_attr = _handler_for_provider(provider)
                usage = usage_trackers[provider]
                try:
                    handler(session, game, client=clients[provider], usage=usage)
                    setattr(game, ts_attr, datetime.now(timezone.utc))
                    session.add(game)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.exception(
                        "Failed direct ingest for game %s provider %s: %s",
                        game.id,
                        provider,
                        exc,
                    )
                    session.rollback()
                else:
                    session.commit()
            processed += 1
            if periodic_every and processed % periodic_every == 0:
                for provider, tracker in usage_trackers.items():
                    record_provider_usage_snapshot(
                        session,
                        provider=provider,
                        credits_remaining=tracker.last_remaining,
                        credits_used=tracker.requests_made,
                        window_label=f"periodic_{window_label}",
                    )
                session.commit()
    logger.info("Direct mode processed %s games", processed)


def _snapshot_run_usage(
    session: Session,
    trackers: dict[str, ProviderUsageContext],
    window_label: str,
) -> None:
    for provider, tracker in trackers.items():
        record_provider_usage_snapshot(
            session,
            provider=provider,
            credits_remaining=tracker.last_remaining,
            credits_used=tracker.requests_made,
            window_label=window_label,
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Unified odds job orchestrator")
    parser.add_argument("--start", required=True, type=_parse_date, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, type=_parse_date, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--providers",
        nargs="+",
        default=list(PROVIDERS),
        help="Providers to include (default: all)",
    )
    parser.add_argument(
        "--create-jobs-only",
        action="store_true",
        help="Only create jobs, do not process them",
    )
    parser.add_argument(
        "--direct-mode",
        action="store_true",
        help="Process games directly instead of using jobs",
    )
    parser.add_argument(
        "--log-usage-every-n-games",
        type=int,
        default=None,
        help="If set, record provider usage snapshots every N games",
    )
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    clients = _load_clients()
    session_factory = create_session_factory(create_db_engine(database_url))

    with get_session(session_factory) as session:
        games = _load_games(session, args.start, args.end)
        logger.info("Found %s games between %s and %s", len(games), args.start, args.end)
        if args.create_jobs_only:
            created, skipped = create_jobs(session, games, args.providers)
            logger.info("Created %s jobs (skipped %s existing)", created, skipped)
            session.commit()
            return

    usage_trackers = {p: ProviderUsageContext(provider=p) for p in args.providers}
    window_label = f"unified_odds_run_{args.start}_to_{args.end}"

    if args.direct_mode:
        run_direct_mode(
            games,
            args.providers,
            session_factory=session_factory,
            clients=clients,
            usage_trackers=usage_trackers,
            periodic_every=args.log_usage_every_n_games,
            window_label=window_label,
        )
    else:
        with get_session(session_factory) as session:
            created, skipped = create_jobs(session, games, args.providers)
            logger.info("Queued %s jobs (skipped %s existing)", created, skipped)

    with get_session(session_factory) as session:
        _snapshot_run_usage(session, usage_trackers, window_label)
        session.commit()
        for provider, tracker in usage_trackers.items():
            logger.info(
                "%s: %s requests this run, last remaining=%s",
                provider,
                tracker.requests_made,
                tracker.last_remaining,
            )


if __name__ == "__main__":  # pragma: no cover
    main()

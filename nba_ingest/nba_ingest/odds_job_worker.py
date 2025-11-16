"""Background worker that processes odds_ingest_jobs safely."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import logging
import os
import time

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
logging.basicConfig(level=logging.INFO, format="[ODDS_WORKER] %(message)s")


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


def _claim_job(session: Session, provider: str | None) -> OddsIngestJob | None:
    stmt = select(OddsIngestJob).where(OddsIngestJob.status == "pending")
    if provider:
        stmt = stmt.where(OddsIngestJob.provider == provider)
    stmt = stmt.order_by(OddsIngestJob.created_at).limit(1).with_for_update(skip_locked=True)
    return session.execute(stmt).scalars().first()


def process_job(
    session_factory,
    job: OddsIngestJob,
    *,
    clients: dict[str, object],
    tracker: ProviderUsageContext,
) -> bool:
    handler, ts_attr = _handler_for_provider(job.provider)
    client = clients.get(job.provider)
    if not client:
        raise RuntimeError(f"Missing client for provider {job.provider}")

    with get_session(session_factory) as session:
        game = session.get(Game, job.game_id)
        if not game:
            raise RuntimeError(f"Game {job.game_id} not found")
        count = handler(session, game, client=client, usage=tracker)
        setattr(game, ts_attr, datetime.now(timezone.utc))
        session.add(game)
        job.status = "done"
        job.last_error = None
        job.updated_at = datetime.now(timezone.utc)
        session.merge(job)
        logger.info(
            "Job %s provider=%s game=%s inserted/updated %s odds rows",
            job.id,
            job.provider,
            job.game_id,
            count,
        )
    return True


def handle_failure(session: Session, job: OddsIngestJob, error: Exception) -> None:
    job.updated_at = datetime.now(timezone.utc)
    job.last_error = str(error)[:500]
    if job.attempts < 3:
        job.status = "pending"
    else:
        job.status = "failed"
    session.merge(job)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Odds ingestion job worker")
    parser.add_argument("--provider", help="Restrict to a single provider", default=None)
    parser.add_argument("--max-jobs", type=int, default=None, help="Stop after processing N jobs")
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=5.0,
        help="Sleep duration when no jobs are available",
    )
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    clients = _load_clients()
    session_factory = create_session_factory(create_db_engine(database_url))
    tracker = ProviderUsageContext(provider=args.provider or "multi")

    processed = 0
    while True:
        with get_session(session_factory) as session:
            job = _claim_job(session, args.provider)
            if not job:
                session.commit()
                logger.info("No pending jobs; sleeping %.1fs", args.sleep_seconds)
                time.sleep(args.sleep_seconds)
                continue
            job.status = "in_progress"
            job.attempts += 1
            job.updated_at = datetime.now(timezone.utc)
            session.merge(job)
            session.commit()
            logger.info(
                "Claimed job %s provider=%s game=%s (attempt %s)",
                job.id,
                job.provider,
                job.game_id,
                job.attempts,
            )
        try:
            success = process_job(session_factory, job, clients=clients, tracker=tracker)
        except Exception as exc:  # pragma: no cover - defensive
            with get_session(session_factory) as session:
                handle_failure(session, job, exc)
                session.commit()
            logger.exception("Job %s failed: %s", job.id, exc)
            continue

        if success:
            processed += 1
            with get_session(session_factory) as session:
                record_provider_usage_snapshot(
                    session,
                    provider=job.provider,
                    credits_remaining=tracker.last_remaining,
                    credits_used=tracker.requests_made,
                    window_label="worker",
                )
                session.commit()
        if args.max_jobs and processed >= args.max_jobs:
            logger.info("Max jobs processed; exiting")
            break


if __name__ == "__main__":  # pragma: no cover
    main()

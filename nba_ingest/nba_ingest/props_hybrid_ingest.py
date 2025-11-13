"""CLI entry point for the hybrid SGO + Odds API props ingestion."""
from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .db import create_db_engine, create_session_factory, get_session
from .odds_api_client import TheOddsApiClient
from .models import Base, Player
from .props_models import Checkpoint, Event, IngestionRun, Prop, PropsBase
from .props_settings import PropsSettings, load_props_settings
from .props_utils import (
    canonicalize_player_name,
    ensure_props_exist,
    normalize_team_name,
    parse_iso_datetime,
    random_validation_sample,
    utc_now,
)
from .sports_game_odds_client import SportsGameOddsClient


LOGGER = logging.getLogger("props_ingest")


def _bool_arg(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered in {"1", "true", "yes", "y"}


def _daterange(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _extract_provider_event_id(payload: Dict[str, object]) -> str:
    for key in ("id", "event_id", "eventId", "provider_event_id"):
        value = payload.get(key)
        if value is not None:
            return str(value)
    raise ValueError("Unable to determine provider event id")


def _extract_commence(payload: Dict[str, object]) -> Optional[datetime]:
    for key in ("commence_time", "commenceTime", "start_time", "startTime"):
        if key in payload:
            parsed = parse_iso_datetime(str(payload[key]))
            if parsed:
                return parsed
    return None


def _extract_team(payload: Dict[str, object], keys: Sequence[str]) -> Optional[str]:
    for key in keys:
        value = payload.get(key)
        if value:
            return str(value)
    return None


def _upsert_event(session: Session, provider: str, payload: Dict[str, object], game_date: date) -> Event:
    provider_event_id = _extract_provider_event_id(payload)
    commence = _extract_commence(payload)
    normalized_home = normalize_team_name(
        _extract_team(payload, ["home_team", "homeTeam", "home", "homeTeamName"])
    )
    normalized_away = normalize_team_name(
        _extract_team(payload, ["away_team", "awayTeam", "away", "awayTeamName"])
    )
    stmt = (
        insert(Event)
        .values(
            provider=provider,
            provider_event_id=provider_event_id,
            sport_key=str(payload.get("sport_key") or payload.get("sportKey") or "basketball_nba"),
            commence_time_utc=commence,
            game_date=game_date,
            home_team=normalized_home,
            away_team=normalized_away,
            season=str(payload.get("season")) if payload.get("season") else None,
        )
        .on_conflict_do_update(
            index_elements=[Event.provider, Event.provider_event_id],
            set_=dict(
                commence_time_utc=commence,
                game_date=game_date,
                home_team=normalized_home,
                away_team=normalized_away,
            ),
        )
        .returning(Event)
    )
    result = session.execute(stmt)
    return result.scalar_one()


def _ensure_checkpoint(session: Session, provider: str, game_date: date, provider_event_id: str) -> None:
    stmt = insert(Checkpoint).values(
        provider=provider,
        date=game_date,
        provider_event_id=provider_event_id,
        status="discovered",
        attempts=0,
    )
    stmt = stmt.on_conflict_do_nothing()
    session.execute(stmt)


def _ensure_player(session: Session, player_name: str) -> Player:
    canonical = canonicalize_player_name(player_name)
    stmt = (
        insert(Player)
        .values(full_name=player_name, canonical_name=canonical)
        .on_conflict_do_update(
            index_elements=[Player.canonical_name],
            set_={"full_name": player_name},
        )
        .returning(Player)
    )
    return session.execute(stmt).scalar_one()


def _upsert_prop(
    session: Session,
    event_id: int,
    player: Player,
    record: Dict[str, object],
) -> None:
    stmt = (
        insert(Prop)
        .values(
            event_id=event_id,
            player_id=player.id,
            market_key=record.get("market_key"),
            line=record.get("line"),
            price=record.get("price"),
            bookmaker_key=record.get("bookmaker_key"),
            last_update_utc=record.get("last_update_utc"),
            provider=record.get("provider"),
            provider_market_key=record.get("provider_market_key"),
        )
        .on_conflict_do_update(
            constraint="uq_props_event_player_market",
            set_={
                "price": record.get("price"),
                "last_update_utc": record.get("last_update_utc"),
                "line": record.get("line"),
                "bookmaker_key": record.get("bookmaker_key"),
            },
        )
    )
    session.execute(stmt)


def _build_prop_records(
    event: Event,
    bookmaker: Dict[str, object],
    market: Dict[str, object],
    outcome: Dict[str, object],
    provider: str,
) -> Optional[Dict[str, object]]:
    player_name = outcome.get("name") or outcome.get("player")
    if not player_name:
        return None
    price = outcome.get("price") or outcome.get("odds")
    line = outcome.get("point") or outcome.get("line")
    if line is not None:
        try:
            line = float(line)
        except (TypeError, ValueError):
            line = None
    if price is not None:
        try:
            price = int(price)
        except (TypeError, ValueError):
            price = None
    last_update = parse_iso_datetime(str(bookmaker.get("last_update")))
    return {
        "player_name": str(player_name),
        "line": line,
        "price": price,
        "market_key": str(market.get("key") or market.get("market_key")),
        "bookmaker_key": str(bookmaker.get("key") or bookmaker.get("title") or "unknown"),
        "last_update_utc": last_update,
        "provider": provider,
        "provider_market_key": str(market.get("key") or market.get("market_key")),
    }


@dataclass
class ProviderStats:
    name: str
    processed: int = 0
    http_429: int = 0
    http_5xx: int = 0
    last_success_ts: Optional[datetime] = None
    processed_history: deque = field(default_factory=lambda: deque(maxlen=120))
    request_history: deque = field(default_factory=lambda: deque(maxlen=120))
    events_since_validation: int = 0

    def record_processed(self) -> None:
        self.processed += 1
        self.events_since_validation += 1
        self.last_success_ts = utc_now()
        self.processed_history.append(time.monotonic())

    def record_rate_limit(self, _provider: str) -> None:
        self.http_429 += 1

    def record_server_error(self, _provider: str) -> None:
        self.http_5xx += 1

    def record_request(self, _provider: str) -> None:
        self.request_history.append(time.monotonic())

    def processed_per_min(self) -> float:
        cutoff = time.monotonic() - 60
        while self.processed_history and self.processed_history[0] < cutoff:
            self.processed_history.popleft()
        return float(len(self.processed_history))

    def requests_per_min(self) -> float:
        cutoff = time.monotonic() - 60
        while self.request_history and self.request_history[0] < cutoff:
            self.request_history.popleft()
        return float(len(self.request_history))

    def reset_validation_counter(self) -> None:
        self.events_since_validation = 0


def discover_events(
    session: Session,
    dates: Iterable[date],
    settings: PropsSettings,
    sgo_client: SportsGameOddsClient,
    odds_client: TheOddsApiClient,
    resume: bool,
) -> int:
    discovered = 0
    split = settings.historical_split
    for day in dates:
        provider = "SGO" if day < split else "ODDS"
        existing = session.execute(
            select(Checkpoint.provider_event_id).where(
                Checkpoint.provider == provider, Checkpoint.date == day
            )
        ).first()
        if existing and resume:
            continue
        if provider == "SGO":
            events = sgo_client.list_events_by_date(day)
        else:
            snapshot = f"{day.isoformat()}T12:00:00Z"
            events = odds_client.list_historical_events_by_date(snapshot)
        for event_payload in events:
            event = _upsert_event(session, provider, event_payload, day)
            _ensure_checkpoint(session, provider, day, event.provider_event_id)
            discovered += 1
    return discovered


def _load_queue(session: Session, max_events: int) -> List[Checkpoint]:
    stmt = (
        select(Checkpoint)
        .where(Checkpoint.status.in_(["discovered", "error"]))
        .order_by(Checkpoint.date, Checkpoint.provider_event_id)
    )
    if max_events:
        stmt = stmt.limit(max_events)
    return session.execute(stmt).scalars().all()


def _mark_checkpoint(
    session: Session,
    checkpoint: Checkpoint,
    *,
    status: str,
    worker: str,
    error: Optional[str] = None,
    increment_attempt: bool = False,
) -> None:
    checkpoint.status = status
    checkpoint.worker = worker
    if increment_attempt:
        checkpoint.attempts = (checkpoint.attempts or 0) + 1
    checkpoint.last_error = error
    session.add(checkpoint)


def _process_props(
    session: Session,
    event: Event,
    props_payload: List[Dict[str, object]],
    provider: str,
) -> int:
    inserted = 0
    for bookmaker in props_payload:
        markets = bookmaker.get("markets") or bookmaker.get("market") or []
        if not isinstance(markets, list):
            continue
        for market in markets:
            outcomes = market.get("outcomes") or []
            if not isinstance(outcomes, list):
                continue
            for outcome in outcomes:
                record = _build_prop_records(event, bookmaker, market, outcome, provider)
                if not record:
                    continue
                player = _ensure_player(session, record.pop("player_name"))
                _upsert_prop(session, event.id, player, record)
                inserted += 1
    return inserted


def _validate_provider_data(session: Session, provider: str) -> None:
    samples = random_validation_sample(session, provider, limit=5)
    if not samples:
        LOGGER.warning("Validation sample for %s returned no events", provider)
        return
    missing = ensure_props_exist(session, samples)
    if missing:
        LOGGER.warning("%s validation found %s events without props", provider, len(missing))


def _log_metrics(
    stats: Dict[str, ProviderStats],
    total_discovered: int,
    total_processed: int,
    remaining: int,
    sgo_quota_used: int,
    settings: PropsSettings,
    odds_client: TheOddsApiClient,
    sgo_client: SportsGameOddsClient,
) -> None:
    LOGGER.info(
        "Totals discovered=%s processed=%s remaining=%s",
        total_discovered,
        total_processed,
        remaining,
    )
    for name, provider_stats in stats.items():
        LOGGER.info(
            "%s worker → processed=%s, per_min=%.2f, 429=%s, 5xx=%s, last_success=%s",
            name,
            provider_stats.processed,
            provider_stats.processed_per_min(),
            provider_stats.http_429,
            provider_stats.http_5xx,
            provider_stats.last_success_ts,
        )
    LOGGER.info(
        "SGO quota used=%s / %s", sgo_quota_used, settings.sgo_objects_per_month
    )
    LOGGER.info(
        "Odds API rate tracker → last_request_ts=%.2f, requests/min=%.2f (limit=%s)",
        odds_client.last_request_ts,
        stats["ODDS"].requests_per_min(),
        settings.odds_reqs_per_min,
    )
    LOGGER.info(
        "SGO rate tracker → last_request_ts=%.2f, requests/min=%.2f (limit=%s)",
        sgo_client.last_request_ts,
        stats["SGO"].requests_per_min(),
        settings.sgo_reqs_per_min,
    )


def _write_report(report_rows: List[Dict[str, object]]) -> Path:
    reports_dir = Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    path = reports_dir / f"props_hybrid_ingest_{timestamp}.csv"
    if not report_rows:
        path.touch()
        return path
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "date",
                "provider",
                "provider_event_id",
                "status",
                "wrote_rows",
                "retries",
                "elapsed_ms",
            ],
        )
        writer.writeheader()
        for row in report_rows:
            writer.writerow(row)
    return path


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hybrid NBA props ingestion")
    parser.add_argument("--start", required=False)
    parser.add_argument("--end", required=False)
    parser.add_argument("--markets", required=False)
    parser.add_argument("--region", required=False)
    parser.add_argument("--include-alt-lines", dest="include_alt_lines", required=False)
    parser.add_argument("--dry-run", dest="dry_run", required=False)
    parser.add_argument("--global-max-events", dest="global_max_events", type=int, required=False)
    parser.add_argument("--timestamp", dest="snapshot_ts", default="12:00:00Z")
    parser.add_argument("--resume", dest="resume", required=False)
    parser.add_argument("--log-interval-seconds", dest="log_interval", type=int, default=300)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    args = parse_args(argv)
    settings = load_props_settings()

    start_date = date.fromisoformat(args.start) if args.start else settings.historical_start
    end_date = date.fromisoformat(args.end) if args.end else settings.historical_end
    if end_date < start_date:
        raise SystemExit("End date must be >= start date")

    markets = (
        args.markets.split(",") if args.markets else settings.prop_markets
    )
    markets = [m.strip() for m in markets if m.strip()]
    if not markets:
        raise SystemExit("No markets provided")

    include_alt_lines = (
        settings.sgo_include_alt_lines
        if args.include_alt_lines is None
        else _bool_arg(args.include_alt_lines)
    )
    dry_run = settings.dry_run if args.dry_run is None else _bool_arg(args.dry_run)
    global_max_events = args.global_max_events or settings.global_max_events
    resume = _bool_arg(args.resume) if args.resume else False
    region = args.region or settings.region

    if not settings.tos_ack_single_account:
        raise SystemExit(
            "TOS_ACK_SINGLE_ACCOUNT must be true in the environment before running the SGO worker."
        )

    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine, tables=[Player.__table__])
    PropsBase.metadata.create_all(engine)
    session_factory = create_session_factory(engine)

    stats = {
        "SGO": ProviderStats("SGO"),
        "ODDS": ProviderStats("ODDS"),
    }

    sgo_client = SportsGameOddsClient(
        settings.sgo_api_key,
        settings.sgo_base_url,
        reqs_per_min=settings.sgo_reqs_per_min,
        on_rate_limit=stats["SGO"].record_rate_limit,
        on_server_error=stats["SGO"].record_server_error,
        on_request=stats["SGO"].record_request,
    )
    odds_client = TheOddsApiClient(
        settings.odds_api_key,
        settings.odds_api_base_url,
        reqs_per_min=settings.odds_reqs_per_min,
        on_rate_limit=stats["ODDS"].record_rate_limit,
        on_server_error=stats["ODDS"].record_server_error,
        on_request=stats["ODDS"].record_request,
    )

    log_interval = max(args.log_interval, 60)
    next_log = time.monotonic() + log_interval

    run_id: Optional[int] = None

    with get_session(session_factory) as session:
        run = IngestionRun(
            provider="HYBRID",
            markets=",".join(markets),
            params={
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
                "region": region,
                "include_alt_lines": include_alt_lines,
                "dry_run": dry_run,
                "resume": resume,
            },
        )
        session.add(run)
        session.flush()
        run_id = run.id

        discovered = discover_events(
            session,
            _daterange(start_date, end_date),
            settings,
            sgo_client,
            odds_client,
            resume,
        )
        run.events_discovered = discovered
        session.commit()

    report_rows: List[Dict[str, object]] = []
    sgo_quota_used = 0
    sgo_quota_stop = False
    processed_total = 0

    with get_session(session_factory) as session:
        queue = _load_queue(session, global_max_events)
        LOGGER.info("Loaded %s checkpoints", len(queue))
        for checkpoint in queue:
            if checkpoint.provider == "SGO" and sgo_quota_stop:
                continue
            event = session.execute(
                select(Event).where(
                    Event.provider == checkpoint.provider,
                    Event.provider_event_id == checkpoint.provider_event_id,
                )
            ).scalar_one_or_none()
            if not event:
                LOGGER.warning("Missing event for checkpoint %s", checkpoint.provider_event_id)
                continue
            start_time = time.monotonic()
            _mark_checkpoint(
                session,
                checkpoint,
                status="processing",
                worker=checkpoint.provider,
                increment_attempt=True,
            )
            wrote_rows = 0
            error: Optional[str] = None
            try:
                if dry_run:
                    time.sleep(1 if checkpoint.provider == "ODDS" else 0.1)
                    wrote_rows = 0
                elif checkpoint.provider == "SGO":
                    payload = sgo_client.get_event_props(
                        checkpoint.provider_event_id,
                        markets,
                        region,
                        include_alt_lines=include_alt_lines,
                    )
                    wrote_rows = _process_props(session, event, payload, "SGO")
                    sgo_quota_used += 1
                else:
                    snapshot = f"{checkpoint.date.isoformat()}T{args.snapshot_ts}"
                    payload = odds_client.historical_event_odds(
                        checkpoint.provider_event_id,
                        snapshot,
                        markets,
                        region,
                    )
                    wrote_rows = _process_props(session, event, payload, "ODDS")
                _mark_checkpoint(session, checkpoint, status="done", worker=checkpoint.provider)
                stats[checkpoint.provider].record_processed()
                processed_total += 1
                if checkpoint.provider == "SGO" and (
                    sgo_quota_used >= settings.sgo_objects_per_month - 50
                ):
                    sgo_quota_stop = True
            except Exception as exc:
                error = str(exc)
                _mark_checkpoint(
                    session,
                    checkpoint,
                    status="error",
                    worker=checkpoint.provider,
                    error=error,
                )
                LOGGER.error("Failed to process %s: %s", checkpoint.provider_event_id, exc)
            finally:
                elapsed_ms = int((time.monotonic() - start_time) * 1000)
                report_rows.append(
                    {
                        "date": checkpoint.date.isoformat(),
                        "provider": checkpoint.provider,
                        "provider_event_id": checkpoint.provider_event_id,
                        "status": checkpoint.status,
                        "wrote_rows": wrote_rows,
                        "retries": checkpoint.attempts,
                        "elapsed_ms": elapsed_ms,
                    }
                )
                session.flush()

            provider_stats = stats[checkpoint.provider]
            if provider_stats.events_since_validation >= 100:
                _validate_provider_data(session, checkpoint.provider)
                provider_stats.reset_validation_counter()

            if time.monotonic() >= next_log:
                remaining = session.execute(
                    select(func.count()).where(Checkpoint.status != "done")
                ).scalar_one()
                _log_metrics(
                    stats,
                    discovered,
                    processed_total,
                    remaining,
                    sgo_quota_used,
                    settings,
                    odds_client,
                    sgo_client,
                )
                next_log = time.monotonic() + log_interval

            if global_max_events and processed_total >= global_max_events:
                LOGGER.warning("GLOBAL_MAX_EVENTS reached; stopping early")
                break

        if run_id is not None:
            run = session.get(IngestionRun, run_id)
        else:
            run = None
        if run:
            run.events_processed = processed_total
            run.http_429_count = stats["SGO"].http_429 + stats["ODDS"].http_429
            run.http_5xx_count = stats["SGO"].http_5xx + stats["ODDS"].http_5xx
            run.finished_at = utc_now()
            session.add(run)

    report_path = _write_report(report_rows)
    LOGGER.info("Wrote ingestion report to %s", report_path)


if __name__ == "__main__":
    main()

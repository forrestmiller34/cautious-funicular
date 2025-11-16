"""Provider-specific odds ingestion helpers and upsert utilities."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .models import Game, UnifiedGameOdds
from .odds_api_client import TheOddsApiClient
from .sports_game_odds_client import SportsGameOddsClient
from .unified_odds_ingest import BetsApiClient

logger = logging.getLogger(__name__)

DEFAULT_SGO_MARKETS: tuple[str, ...] = (
    "player_points",
    "player_assists",
    "player_rebounds",
    "player_threes",
)
DEFAULT_ODDS_API_MARKETS: tuple[str, ...] = ("h2h", "spreads", "totals")
DEFAULT_BETSAPI_MARKETS: tuple[str, ...] = ("moneyline", "spread", "total")


@dataclass(slots=True)
class ProviderUsageContext:
    provider: str
    requests_made: int = 0
    last_remaining: float | None = None

    def record(self, remaining: float | None = None) -> None:
        self.requests_made += 1
        if remaining is not None:
            self.last_remaining = remaining


class UpsertHelper:
    """Perform provider odds upserts with the unified unique constraint."""

    @staticmethod
    def upsert_odds(session: Session, rows: Sequence[dict]) -> int:
        if not rows:
            return 0
        if session.bind and session.bind.dialect.name == "postgresql":
            stmt = insert(UnifiedGameOdds).values(rows)
            update_cols = {
                "line": stmt.excluded.line,
                "price": stmt.excluded.price,
                "extra": stmt.excluded.extra,
                "updated_at": datetime.now(timezone.utc),
            }
            stmt = stmt.on_conflict_do_update(
                constraint="uq_unified_odds_identity", set_=update_cols
            )
            result = session.execute(stmt)
            return result.rowcount or len(rows)

        affected = 0
        for row in rows:
            existing = session.execute(
                select(UnifiedGameOdds).where(
                    UnifiedGameOdds.provider == row["provider"],
                    UnifiedGameOdds.game_id == row["game_id"],
                    UnifiedGameOdds.market_type == row["market_type"],
                    UnifiedGameOdds.bookmaker == row["bookmaker"],
                    UnifiedGameOdds.outcome_key == row["outcome_key"],
                )
            ).scalar_one_or_none()
            if existing:
                existing.line = row.get("line")
                existing.price = row.get("price")
                existing.extra = row.get("extra")
                existing.updated_at = datetime.now(timezone.utc)
            else:
                session.add(UnifiedGameOdds(**row))
            affected += 1
        return affected


def _coerce_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve_event_id(game: Game, *, attr: str) -> str:
    explicit = getattr(game, attr, None)
    if explicit:
        return str(explicit)
    if isinstance(game.provider_event_ids, dict):
        value = game.provider_event_ids.get(attr) or game.provider_event_ids.get(attr.replace("_event_id", ""))
        if value:
            return str(value)
    raise ValueError(f"Missing provider event id for {attr}")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ingest_sgo_odds_for_game(
    session: Session,
    game: Game,
    *,
    client: SportsGameOddsClient,
    region: str = "us",
    markets: Iterable[str] = DEFAULT_SGO_MARKETS,
    include_alt_lines: bool = False,
    usage: ProviderUsageContext | None = None,
) -> int:
    event_id = _resolve_event_id(game, attr="sgo_event_id")
    bookmakers = client.get_event_props(
        event_id,
        markets,
        region,
        include_alt_lines=include_alt_lines,
    )
    if usage:
        usage.record()

    rows: list[dict] = []
    for bookmaker in bookmakers:
        book_key = str(bookmaker.get("key") or bookmaker.get("title") or "sgo")
        for market in bookmaker.get("markets", []):
            market_key = str(market.get("key") or market.get("market") or "unknown")
            for outcome in market.get("outcomes", []):
                outcome_key = str(
                    outcome.get("type")
                    or outcome.get("description")
                    or outcome.get("name")
                    or "unknown"
                )
                rows.append(
                    {
                        "provider": "sgo",
                        "game_id": game.id,
                        "market_type": market_key,
                        "bookmaker": book_key,
                        "outcome_key": outcome_key,
                        "line": _coerce_float(outcome.get("point") or outcome.get("line")),
                        "price": _coerce_float(outcome.get("price") or outcome.get("odds")),
                        "created_at": _now_utc(),
                        "updated_at": _now_utc(),
                        "extra": {
                            "bookmaker_title": bookmaker.get("title"),
                            "raw_outcome": outcome,
                        },
                    }
                )
    return UpsertHelper.upsert_odds(session, rows)


def ingest_oddsapi_odds_for_game(
    session: Session,
    game: Game,
    *,
    client: TheOddsApiClient,
    markets: Iterable[str] = DEFAULT_ODDS_API_MARKETS,
    region: str = "us",
    usage: ProviderUsageContext | None = None,
) -> int:
    event_id = _resolve_event_id(game, attr="odds_api_event_id")
    snapshot_iso = f"{game.game_date.isoformat()}T00:00:00Z"
    payload = client.historical_event_odds(event_id, snapshot_iso, markets, region)
    if usage:
        usage.record(_extract_remaining(payload))

    rows: list[dict] = []
    for bookmaker in payload or []:
        book_key = str(bookmaker.get("key") or bookmaker.get("title") or "oddsapi")
        for market in bookmaker.get("markets", []):
            market_key = str(market.get("key") or market.get("market") or "unknown")
            for outcome in market.get("outcomes", []):
                outcome_key = str(outcome.get("name") or outcome.get("description") or outcome.get("type") or "unknown")
                rows.append(
                    {
                        "provider": "oddsapi",
                        "game_id": game.id,
                        "market_type": market_key,
                        "bookmaker": book_key,
                        "outcome_key": outcome_key,
                        "line": _coerce_float(outcome.get("point") or outcome.get("line")),
                        "price": _coerce_float(outcome.get("price")),
                        "created_at": _now_utc(),
                        "updated_at": _now_utc(),
                        "extra": {
                            "raw_outcome": outcome,
                            "region": region,
                        },
                    }
                )
    return UpsertHelper.upsert_odds(session, rows)


def ingest_betsapi_odds_for_game(
    session: Session,
    game: Game,
    *,
    client: BetsApiClient,
    markets: Iterable[str] = DEFAULT_BETSAPI_MARKETS,
    usage: ProviderUsageContext | None = None,
) -> int:
    event_id = (
        game.provider_event_ids.get("betsapi")
        if isinstance(game.provider_event_ids, dict)
        else None
    )
    if not event_id:
        event_id = getattr(game, "betsapi_event_id", None)  # type: ignore[attr-defined]
    if not event_id:
        raise ValueError("Missing betsapi event id")
    payload = client.fetch_event_odds(str(event_id))
    if usage:
        usage.record()

    rows: list[dict] = []
    for market in payload.get("results", []) if isinstance(payload, dict) else payload or []:
        market_key = str(market.get("market") or market.get("key") or "unknown")
        for outcome in market.get("odds", []) if isinstance(market, dict) else []:
            outcome_key = str(outcome.get("label") or outcome.get("name") or outcome.get("type") or "unknown")
            rows.append(
                {
                    "provider": "betsapi",
                    "game_id": game.id,
                    "market_type": market_key,
                    "bookmaker": str(market.get("bookmaker") or market.get("book") or "betsapi"),
                    "outcome_key": outcome_key,
                    "line": _coerce_float(outcome.get("handicap") or outcome.get("line")),
                    "price": _coerce_float(outcome.get("odds") or outcome.get("price")),
                    "created_at": _now_utc(),
                    "updated_at": _now_utc(),
                    "extra": {"raw_outcome": outcome},
                }
            )
    return UpsertHelper.upsert_odds(session, rows)


def _extract_remaining(payload: object) -> float | None:
    if isinstance(payload, dict):
        for key in ("remaining", "credits_remaining", "requests_remaining"):
            value = payload.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
    return None


__all__ = [
    "ProviderUsageContext",
    "ingest_sgo_odds_for_game",
    "ingest_oddsapi_odds_for_game",
    "ingest_betsapi_odds_for_game",
    "UpsertHelper",
]

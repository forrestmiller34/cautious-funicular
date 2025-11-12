"""CLI helpers for fetching and storing betting odds from Ball Don't Lie."""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone, date
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .balldontlie_client import BallDontLieClient
from .config import load_settings
from .db import create_db_engine, create_session_factory, get_session
from .models import Base, NBAGame, NBAGameOdds
from .odds_math import decimal_to_american

DATE_FORMAT = "%Y-%m-%d"


def _parse_iso_datetime(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except Exception:
        return datetime.now(timezone.utc)


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        value = value.strip()
        try:
            return float(value)
        except ValueError:
            match = re.search(r"-?\d+(?:\.\d+)?", value)
            if match:
                try:
                    return float(match.group(0))
                except ValueError:
                    return None
    return None


def _coerce_american(value: Any) -> Optional[int]:
    if isinstance(value, dict):
        for key in ("american", "american_odds", "price", "odds", "moneyline", "line", "value"):
            if key in value and value[key] is not None:
                return _coerce_american(value[key])
        return None
    if value is None:
        return None
    if isinstance(value, int):
        if value == 0:
            return None
        return value
    if isinstance(value, float):
        if value == 0:
            return None
        if 1.01 <= value < 10:
            return decimal_to_american(value)
        return int(round(value))
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        if value.startswith("+"):
            value = value[1:]
        try:
            numeric = float(value)
        except ValueError:
            return None
        return _coerce_american(numeric)
    return None


def _normalize_market_type(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    normalized = value.lower()
    if normalized in {"moneyline", "ml", "h2h", "head_to_head"}:
        return "moneyline"
    if normalized in {"spread", "spreads", "point_spread", "handicap"}:
        return "spread"
    if normalized in {"total", "totals", "over_under", "overunder", "game_total"}:
        return "total"
    return normalized


def _extract_vendor_name(vendor_data: Dict[str, Any]) -> str:
    for key in ("vendor", "name", "title", "bookmaker", "key"):
        value = vendor_data.get(key)
        if value:
            return str(value)
    return "unknown"


def _identify_side(outcome: Dict[str, Any], context: Dict[str, Any]) -> Optional[str]:
    for key in ("team_id", "participant_id", "id"):
        if key in outcome and outcome[key] is not None:
            candidate = outcome[key]
            if candidate == context["home_id"]:
                return "home"
            if candidate == context["away_id"]:
                return "away"
    nested_team = outcome.get("team")
    if isinstance(nested_team, dict):
        team_id = nested_team.get("id")
        if team_id == context["home_id"]:
            return "home"
        if team_id == context["away_id"]:
            return "away"
        team_name = str(nested_team.get("name") or "").lower()
        if team_name:
            if context["home_name"].lower() in team_name or context["home_abbr"].lower() in team_name:
                return "home"
            if context["away_name"].lower() in team_name or context["away_abbr"].lower() in team_name:
                return "away"
    labels = [
        outcome.get("name"),
        outcome.get("label"),
        outcome.get("description"),
        outcome.get("short_name"),
        outcome.get("side"),
        outcome.get("team"),
    ]
    for label in labels:
        if not label:
            continue
        text = str(label).lower()
        if "over" in text:
            return "over"
        if "under" in text:
            return "under"
        if context["home_name"].lower() in text or context["home_abbr"].lower() in text:
            return "home"
        if context["away_name"].lower() in text or context["away_abbr"].lower() in text:
            return "away"
        if text in {"home", "home team"}:
            return "home"
        if text in {"away", "away team"}:
            return "away"
    return None


def _build_moneyline_record(
    outcomes: List[Dict[str, Any]], context: Dict[str, Any], vendor: str, last_update: datetime
) -> Optional[Dict[str, Any]]:
    home_price = None
    away_price = None
    for outcome in outcomes:
        side = _identify_side(outcome, context)
        price = _coerce_american(outcome.get("price"))
        if price is None:
            price = _coerce_american(outcome.get("odds"))
        if price is None:
            price = _coerce_american(outcome)
        if side == "home" and price is not None:
            home_price = price
        elif side == "away" and price is not None:
            away_price = price
    if home_price is None and away_price is None:
        return None
    return {
        "game_id": context["game_id"],
        "vendor": vendor,
        "line_type": "moneyline",
        "home_team_price": home_price,
        "away_team_price": away_price,
        "last_update": last_update,
    }


def _build_spread_records(
    outcomes: List[Dict[str, Any]], context: Dict[str, Any], vendor: str, last_update: datetime
) -> List[Dict[str, Any]]:
    grouped: Dict[float, Dict[str, Optional[int]]] = {}
    for outcome in outcomes:
        side = _identify_side(outcome, context)
        if side not in {"home", "away"}:
            continue
        price = _coerce_american(outcome.get("price"))
        if price is None:
            price = _coerce_american(outcome.get("odds"))
        if price is None:
            price = _coerce_american(outcome)
        point = _coerce_float(outcome.get("point"))
        if point is None:
            point = _coerce_float(outcome.get("spread"))
        if point is None:
            point = _coerce_float(outcome.get("line"))
        if point is None:
            point = _coerce_float(outcome)
        if point is None:
            continue
        key = round(point if side == "home" else -point, 3)
        entry = grouped.setdefault(key, {"home": None, "away": None})
        entry[side] = price
    records = []
    for spread_point, entry in grouped.items():
        records.append(
            {
                "game_id": context["game_id"],
                "vendor": vendor,
                "line_type": "spread",
                "home_team_price": entry.get("home"),
                "away_team_price": entry.get("away"),
                "spread_points": spread_point,
                "last_update": last_update,
            }
        )
    return records


def _build_total_records(
    outcomes: List[Dict[str, Any]], context: Dict[str, Any], vendor: str, last_update: datetime
) -> List[Dict[str, Any]]:
    grouped: Dict[float, Dict[str, Optional[int]]] = {}
    for outcome in outcomes:
        side = _identify_side(outcome, context)
        if side not in {"over", "under"}:
            continue
        price = _coerce_american(outcome.get("price"))
        if price is None:
            price = _coerce_american(outcome.get("odds"))
        if price is None:
            price = _coerce_american(outcome)
        point = _coerce_float(outcome.get("point"))
        if point is None:
            point = _coerce_float(outcome.get("total"))
        if point is None:
            point = _coerce_float(outcome.get("line"))
        if point is None:
            point = _coerce_float(outcome)
        if point is None:
            continue
        key = round(point, 3)
        entry = grouped.setdefault(key, {"over": None, "under": None})
        entry[side] = price
    records = []
    for total_point, entry in grouped.items():
        records.append(
            {
                "game_id": context["game_id"],
                "vendor": vendor,
                "line_type": "total",
                "total_points": total_point,
                "over_price": entry.get("over"),
                "under_price": entry.get("under"),
                "last_update": last_update,
            }
        )
    return records


def _prepare_vendor_markets(vendor_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    markets = vendor_data.get("markets")
    if isinstance(markets, list):
        return markets
    # Some payloads may list moneyline/spread/total directly on the vendor object.
    collected: List[Dict[str, Any]] = []
    for key in ("moneyline", "spread", "spreads", "total", "totals", "over_under"):
        if key in vendor_data and isinstance(vendor_data[key], dict):
            market = dict(vendor_data[key])
            market.setdefault("key", key)
            collected.append(market)
    return collected


def _prepare_vendor_entries(odds_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    vendors = odds_payload.get("bookmakers") or odds_payload.get("vendors")
    if isinstance(vendors, list) and vendors:
        return vendors
    vendor_name = odds_payload.get("vendor") or odds_payload.get("bookmaker") or odds_payload.get("provider")
    markets = odds_payload.get("markets")
    if not isinstance(markets, list):
        markets = []
    return [
        {
            "name": vendor_name or "unknown",
            "markets": markets,
            "last_update": odds_payload.get("last_update") or odds_payload.get("updated_at"),
        }
    ]


def _build_records_for_payload(odds_payload: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    vendor_entries = _prepare_vendor_entries(odds_payload)
    for vendor_data in vendor_entries:
        vendor_name = _extract_vendor_name(vendor_data)
        last_update = _parse_iso_datetime(
            vendor_data.get("last_update")
            or vendor_data.get("updated_at")
            or odds_payload.get("last_update")
            or odds_payload.get("updated_at")
        )
        markets = _prepare_vendor_markets(vendor_data)
        for market in markets:
            market_type = _normalize_market_type(market.get("key") or market.get("market_type") or market.get("type"))
            if not market_type:
                continue
            outcomes = market.get("outcomes") or market.get("lines")
            if not isinstance(outcomes, list):
                continue
            if market_type == "moneyline":
                record = _build_moneyline_record(outcomes, context, vendor_name, last_update)
                if record:
                    records.append(record)
            elif market_type == "spread":
                records.extend(_build_spread_records(outcomes, context, vendor_name, last_update))
            elif market_type == "total":
                records.extend(_build_total_records(outcomes, context, vendor_name, last_update))
    return records


def _load_game_context(session: Session, game_ids: Sequence[int]) -> Dict[int, Dict[str, Any]]:
    if not game_ids:
        return {}
    games = (
        session.execute(
            select(NBAGame)
            .where(NBAGame.id.in_(game_ids))
            .options(joinedload(NBAGame.home_team), joinedload(NBAGame.visitor_team))
        )
        .unique()
        .scalars()
        .all()
    )
    context: Dict[int, Dict[str, Any]] = {}
    for game in games:
        home = game.home_team
        away = game.visitor_team
        context[game.id] = {
            "game_id": game.id,
            "home_id": game.home_team_id,
            "away_id": game.visitor_team_id,
            "home_name": (home.full_name if home else "HOME").strip(),
            "away_name": (away.full_name if away else "AWAY").strip(),
            "home_abbr": (home.abbreviation if home else "H").strip(),
            "away_abbr": (away.abbreviation if away else "A").strip(),
        }
    return context


def ingest_odds_payloads(session: Session, payloads: Iterable[Dict[str, Any]]) -> Tuple[int, int]:
    """Upsert odds payloads into the database.

    Returns a tuple of (inserted_count, updated_count).
    """
    payload_list = list(payloads)
    game_ids = [payload.get("game", {}).get("id") for payload in payload_list if payload.get("game")]
    context_map = _load_game_context(session, [gid for gid in game_ids if gid is not None])
    inserted = 0
    updated = 0
    for payload in payload_list:
        game_info = payload.get("game")
        if not isinstance(game_info, dict):
            continue
        game_id = game_info.get("id")
        if game_id not in context_map:
            continue
        context = context_map[game_id]
        records = _build_records_for_payload(payload, context)
        for record in records:
            filters = [
                NBAGameOdds.game_id == record["game_id"],
                NBAGameOdds.vendor == record["vendor"],
                NBAGameOdds.line_type == record["line_type"],
            ]
            if record["line_type"] == "spread":
                filters.append(NBAGameOdds.spread_points == record.get("spread_points"))
            elif record["line_type"] == "total":
                filters.append(NBAGameOdds.total_points == record.get("total_points"))
            existing = session.execute(select(NBAGameOdds).where(*filters).limit(1)).scalar_one_or_none()
            if existing:
                for key, value in record.items():
                    setattr(existing, key, value)
                updated += 1
            else:
                session.add(NBAGameOdds(**record))
                inserted += 1
    return inserted, updated


def main(argv: Optional[Iterable[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Fetch and cache NBA betting odds.")
    parser.add_argument(
        "--date",
        type=lambda value: datetime.strptime(value, DATE_FORMAT).date(),
        help="Target date in YYYY-MM-DD format (defaults to today).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    target_date: date = args.date or datetime.now(timezone.utc).date()
    print(f"Fetching odds for {target_date:%Y-%m-%d}…")

    settings = load_settings()
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    client = BallDontLieClient(settings.api_key)

    payloads = client.list_odds_by_date(target_date.strftime(DATE_FORMAT))
    if not payloads:
        print("No odds payloads returned for the requested date.")
        return

    with get_session(session_factory) as session:
        inserted, updated = ingest_odds_payloads(session, payloads)
        session.flush()
    print(f"Odds upsert complete. Inserted {inserted} rows, updated {updated} rows.")


if __name__ == "__main__":
    main()


__all__ = ["ingest_odds_payloads", "main"]

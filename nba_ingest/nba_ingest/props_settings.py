"""Settings helpers for the hybrid props ingestion CLI."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import os
from typing import List

from dotenv import load_dotenv


def _parse_bool(value: str | bool | None, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _parse_int(value: str | None, *, default: int) -> int:
    if value is None:
        return default
    return int(value.strip())


def _parse_date(value: str | None, *, default: date | None = None) -> date:
    if value is None:
        if default is None:
            raise RuntimeError("Missing required date configuration value")
        return default
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()


def _parse_csv(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(slots=True)
class PropsSettings:
    database_url: str
    sgo_api_key: str
    sgo_base_url: str
    sgo_objects_per_month: int
    sgo_reqs_per_min: int
    sgo_include_alt_lines: bool
    odds_api_key: str
    odds_api_base_url: str
    odds_reqs_per_min: int
    prop_markets: List[str]
    region: str
    historical_start: date
    historical_split: date
    historical_end: date
    dry_run: bool
    global_max_events: int
    tos_ack_single_account: bool


def load_props_settings() -> PropsSettings:
    load_dotenv()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    sgo_api_key = os.environ.get("SGO_API_KEY")
    if not sgo_api_key:
        raise RuntimeError("SGO_API_KEY is required")

    odds_api_key = os.environ.get("ODDS_API_KEY")
    if not odds_api_key:
        raise RuntimeError("ODDS_API_KEY is required")

    prop_markets_raw = os.environ.get("PROP_MARKETS")
    if not prop_markets_raw:
        raise RuntimeError("PROP_MARKETS is required")

    settings = PropsSettings(
        database_url=database_url,
        sgo_api_key=sgo_api_key,
        sgo_base_url=os.environ.get("SGO_BASE_URL", "https://api.sportsgameodds.com"),
        sgo_objects_per_month=_parse_int(
            os.environ.get("SGO_OBJECTS_PER_MONTH"), default=2500
        ),
        sgo_reqs_per_min=_parse_int(os.environ.get("SGO_REQS_PER_MIN"), default=10),
        sgo_include_alt_lines=_parse_bool(os.environ.get("SGO_INCLUDE_ALT_LINES"), default=False),
        odds_api_key=odds_api_key,
        odds_api_base_url=os.environ.get("ODDS_API_BASE_URL", "https://api.the-odds-api.com/v4"),
        odds_reqs_per_min=_parse_int(os.environ.get("ODDS_REQS_PER_MIN"), default=30),
        prop_markets=_parse_csv(prop_markets_raw),
        region=os.environ.get("REGION", "us"),
        historical_start=_parse_date(os.environ.get("HISTORICAL_START"), default=date(2021, 10, 19)),
        historical_split=_parse_date(os.environ.get("HISTORICAL_SPLIT"), default=date(2023, 5, 3)),
        historical_end=_parse_date(os.environ.get("HISTORICAL_END"), default=date(2025, 11, 11)),
        dry_run=_parse_bool(os.environ.get("DRY_RUN"), default=False),
        global_max_events=_parse_int(os.environ.get("GLOBAL_MAX_EVENTS"), default=7000),
        tos_ack_single_account=_parse_bool(
            os.environ.get("TOS_ACK_SINGLE_ACCOUNT"), default=False
        ),
    )

    if not settings.prop_markets:
        raise RuntimeError("PROP_MARKETS must include at least one market key")

    return settings


__all__ = ["PropsSettings", "load_props_settings"]

"""Configuration loading utilities for the NBA ingestion project."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from typing import List

from dotenv import load_dotenv


@dataclass(slots=True)
class Settings:
    """Application configuration loaded from environment variables."""

    api_key: str
    database_url: str
    seasons: List[int]


@dataclass(slots=True)
class InjuriesSettings:
    """Configuration for the NBA injuries RapidAPI feed."""

    database_url: str
    api_key: str
    host: str
    base_url: str


def _require_env(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(f"Missing {key} environment variable.")
    return value


def _balldontlie_api_key() -> str:
    for env_name in ("BALDONTLIE_API_KEY", "BALLDONTLIE_API_KEY"):
        value = os.environ.get(env_name)
        if value:
            return value
    raise RuntimeError(
        "Missing BALDONTLIE_API_KEY environment variable (BALLDONTLIE_API_KEY is also accepted)."
    )


def _default_seasons(num_seasons: int = 4) -> List[int]:
    current_year = datetime.utcnow().year
    # NBA seasons span two calendar years, but API expects the starting year.
    return [current_year - i for i in range(1, num_seasons + 1)]


def load_settings() -> Settings:
    """Load application settings from environment variables and .env file."""

    load_dotenv()

    api_key = _balldontlie_api_key()
    database_url = _require_env("DATABASE_URL")

    seasons_raw = os.environ.get("NBA_SEASONS")
    if seasons_raw:
        seasons = [int(season.strip()) for season in seasons_raw.split(",") if season.strip()]
    else:
        seasons = _default_seasons()

    if not seasons:
        raise RuntimeError("NBA seasons list cannot be empty.")

    return Settings(api_key=api_key, database_url=database_url, seasons=seasons)


def load_injuries_settings() -> InjuriesSettings:
    """Load configuration for the NBA injuries ingestion."""

    load_dotenv()
    database_url = _require_env("DATABASE_URL")
    api_key = _require_env("NBA_INJURIES_RAPIDAPI_KEY")
    host = _require_env("NBA_INJURIES_RAPIDAPI_HOST")
    base_url = os.environ.get(
        "NBA_INJURIES_BASE_URL", "https://nba-injuries-reports.p.rapidapi.com"
    )
    return InjuriesSettings(
        database_url=database_url,
        api_key=api_key,
        host=host,
        base_url=base_url.rstrip("/"),
    )


__all__ = [
    "Settings",
    "InjuriesSettings",
    "load_settings",
    "load_injuries_settings",
]

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


def _default_seasons(num_seasons: int = 4) -> List[int]:
    current_year = datetime.utcnow().year
    # NBA seasons span two calendar years, but API expects the starting year.
    return [current_year - i for i in range(1, num_seasons + 1)]


def load_settings() -> Settings:
    """Load application settings from environment variables and .env file."""

    load_dotenv()

    api_key = os.environ.get("BALDONTLIE_API_KEY")
    if not api_key:
        raise RuntimeError("Missing BALDONTLIE_API_KEY environment variable.")

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Missing DATABASE_URL environment variable.")

    seasons_raw = os.environ.get("NBA_SEASONS")
    if seasons_raw:
        seasons = [int(season.strip()) for season in seasons_raw.split(",") if season.strip()]
    else:
        seasons = _default_seasons()

    if not seasons:
        raise RuntimeError("NBA seasons list cannot be empty.")

    return Settings(api_key=api_key, database_url=database_url, seasons=seasons)


__all__ = ["Settings", "load_settings"]

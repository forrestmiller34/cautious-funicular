"""Helpers for canonicalizing player and team names plus season labels."""
from __future__ import annotations

import re
import unicodedata
from datetime import date


_TEAM_SANITIZE = re.compile(r"[^a-z0-9\s]")
_PLAYER_SANITIZE = re.compile(r"[^a-z0-9\s]")


def canonicalize_team_name(name: str | None) -> str:
    """Return a canonicalized identifier for team comparisons."""
    if not name:
        return "unknown"
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    lowered = normalized.lower().strip()
    lowered = _TEAM_SANITIZE.sub(" ", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()


def canonicalize_player_name(name: str | None) -> str:
    """Return a canonicalized identifier for players."""
    if not name:
        return "unknown"
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower().replace(".", " ")
    normalized = _PLAYER_SANITIZE.sub(" ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def season_label(season_start: int | None) -> str | None:
    """Convert a numeric season start (e.g., 2021) into "2021-22"."""
    if season_start is None:
        return None
    end_year = (season_start + 1) % 100
    return f"{season_start}-{end_year:02d}"


def local_game_date(iso_datetime: str | None) -> date | None:
    """Best effort conversion of ISO datetimes into UTC date objects."""
    if not iso_datetime:
        return None
    cleaned = iso_datetime.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = date.fromisoformat(cleaned.split("T")[0])
    except ValueError:
        return None
    return dt


__all__ = [
    "canonicalize_team_name",
    "canonicalize_player_name",
    "season_label",
    "local_game_date",
]

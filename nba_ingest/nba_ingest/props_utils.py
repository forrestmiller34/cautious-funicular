"""Utility helpers shared across the props ingestion pipeline."""
from __future__ import annotations

import random
import re
import unicodedata
from datetime import datetime, timezone
from typing import Iterable, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .props_models import Event, Prop


_TEAM_SANITIZE = re.compile(r"[^a-z0-9\s]")


def normalize_team_name(name: str | None) -> str | None:
    if not name:
        return None
    lowered = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    lowered = lowered.lower().strip()
    lowered = _TEAM_SANITIZE.sub("", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def canonicalize_player_name(name: str | None) -> str:
    if not name:
        return "unknown"
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.replace(".", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip().lower()
    return normalized


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def random_validation_sample(session: Session, provider: str, limit: int = 5) -> list[int]:
    event_ids = session.execute(
        select(Event.id).where(Event.props.any(), Event.provider == provider)
    ).scalars().all()
    if not event_ids:
        return []
    return random.sample(event_ids, min(limit, len(event_ids)))


def ensure_props_exist(session: Session, event_ids: Sequence[int]) -> list[int]:
    missing: list[int] = []
    for event_id in event_ids:
        count = session.execute(
            select(func.count(Prop.id)).where(Prop.event_id == event_id)
        ).scalar_one()
        if count == 0:
            missing.append(event_id)
    return missing


__all__ = [
    "normalize_team_name",
    "canonicalize_player_name",
    "parse_iso_datetime",
    "utc_now",
    "random_validation_sample",
    "ensure_props_exist",
]

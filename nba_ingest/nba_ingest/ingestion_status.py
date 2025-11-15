"""Helpers for tracking ingestion completion at the season level."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import IngestionStatus


def is_ingestion_complete(
    session: Session, source: str, season: int, data_type: str
) -> bool:
    """Return True if the given ingestion triple has already completed."""
    stmt = select(IngestionStatus.id).where(
        IngestionStatus.source == source,
        IngestionStatus.season == season,
        IngestionStatus.data_type == data_type,
    )
    return session.execute(stmt).scalar_one_or_none() is not None


def mark_ingestion_complete(
    session: Session, source: str, season: int, data_type: str
) -> None:
    """Mark the ingestion triple as complete, recording the completion timestamp."""
    stmt = select(IngestionStatus).where(
        IngestionStatus.source == source,
        IngestionStatus.season == season,
        IngestionStatus.data_type == data_type,
    )
    record = session.execute(stmt).scalar_one_or_none()
    completed_at = datetime.now(timezone.utc)
    if record:
        record.completed_at = completed_at
    else:
        record = IngestionStatus(
            source=source,
            season=season,
            data_type=data_type,
            completed_at=completed_at,
        )
        session.add(record)
    session.commit()

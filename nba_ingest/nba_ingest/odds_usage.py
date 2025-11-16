"""Utilities for recording and reporting provider usage snapshots."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import OddsProviderUsage


def record_provider_usage_snapshot(
    session: Session,
    *,
    provider: str,
    credits_remaining: float | None,
    credits_used: float | None,
    window_label: str | None = None,
    meta: str | None = None,
) -> OddsProviderUsage:
    snapshot = OddsProviderUsage(
        provider=provider,
        snapshot_time=datetime.now(timezone.utc),
        credits_remaining=credits_remaining,
        credits_used=credits_used,
        window_label=window_label,
        meta=meta,
    )
    session.add(snapshot)
    return snapshot


def latest_usage(session: Session, provider: str) -> OddsProviderUsage | None:
    return (
        session.execute(
            select(OddsProviderUsage)
            .where(OddsProviderUsage.provider == provider)
            .order_by(OddsProviderUsage.snapshot_time.desc())
            .limit(1)
        ).scalar_one_or_none()
    )


def credits_used_since(session: Session, provider: str, since: datetime) -> float:
    result = session.execute(
        select(func.coalesce(func.sum(OddsProviderUsage.credits_used), 0)).where(
            OddsProviderUsage.provider == provider,
            OddsProviderUsage.snapshot_time >= since,
        )
    ).scalar_one()
    return float(result or 0)


def summarize_usage_by_provider(session: Session, providers: Iterable[str]) -> dict[str, dict]:
    summaries: dict[str, dict] = {}
    for provider in providers:
        last = latest_usage(session, provider)
        summaries[provider] = {
            "last_snapshot": last.snapshot_time if last else None,
            "last_remaining": float(last.credits_remaining) if last and last.credits_remaining is not None else None,
            "last_window": last.window_label if last else None,
            "used_24h": credits_used_since(session, provider, datetime.now(timezone.utc) - timedelta(days=1)),
        }
    return summaries


__all__ = [
    "record_provider_usage_snapshot",
    "latest_usage",
    "credits_used_since",
    "summarize_usage_by_provider",
]

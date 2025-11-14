from __future__ import annotations

from datetime import date
from pathlib import Path
import sys
from typing import Any, Dict, List

import pytest
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nba_ingest.sports_game_odds_client import SportsGameOddsClient


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleep_calls: List[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, value: float) -> None:
        self.sleep_calls.append(value)
        self.now += value


class FakeResponse:
    def __init__(self, status_code: int, payload: Dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = b"{}" if status_code == 200 else b""

    def json(self) -> Dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")


def test_rate_limit_spacing() -> None:
    clock = FakeClock()
    client = SportsGameOddsClient(
        api_key="abc",
        base_url="https://example.com",
        reqs_per_min=30,
        clock=clock.time,
        sleeper=clock.sleep,
    )

    responses = [
        FakeResponse(200, {"events": []}),
        FakeResponse(200, {"events": []}),
    ]

    def _request(*args: Any, **kwargs: Any) -> FakeResponse:
        return responses.pop(0)

    client._session.request = _request  # type: ignore[assignment]

    client.list_events_by_date(date(2023, 1, 1))
    client.list_events_by_date(date(2023, 1, 2))

    assert any(sleep >= 2.0 for sleep in clock.sleep_calls)


def test_retries_on_server_errors() -> None:
    clock = FakeClock()
    client = SportsGameOddsClient(
        api_key="abc",
        base_url="https://example.com",
        reqs_per_min=10,
        clock=clock.time,
        sleeper=clock.sleep,
    )

    responses = [
        FakeResponse(500, {}),
        FakeResponse(200, {"bookmakers": []}),
    ]

    def _request(*args: Any, **kwargs: Any) -> FakeResponse:
        response = responses.pop(0)
        response.content = b"{}"
        return response

    client._session.request = _request  # type: ignore[assignment]

    result = client.get_event_props("123", markets=["player_points"], region="us")

    assert result == []
    assert any(sleep >= 2 for sleep in clock.sleep_calls)

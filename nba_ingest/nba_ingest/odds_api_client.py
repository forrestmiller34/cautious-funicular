"""Client for The Odds API historical player prop data."""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, Iterable, List, Optional

import requests


class TheOddsApiClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        timeout: float = 30.0,
        reqs_per_min: int = 30,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
        on_rate_limit: Callable[[str], None] | None = None,
        on_server_error: Callable[[str], None] | None = None,
        on_request: Callable[[str], None] | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        if not base_url:
            raise ValueError("base_url is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.sport_key = "basketball_nba"
        self.timeout = timeout
        self._session = requests.Session()
        self._min_interval = 60.0 / max(reqs_per_min, 1)
        self._clock = clock or time.monotonic
        self._sleep = sleeper or time.sleep
        self._last_request_ts = 0.0
        self._on_rate_limit = on_rate_limit or (lambda _: None)
        self._on_server_error = on_server_error or (lambda _: None)
        self._on_request = on_request or (lambda _: None)

    @property
    def last_request_ts(self) -> float:
        return self._last_request_ts

    def _respect_rate_limit(self) -> None:
        now = self._clock()
        wait = self._min_interval - (now - self._last_request_ts)
        if wait > 0:
            self._sleep(wait)

    def _request(self, path: str, *, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{path}"
        attempts_429 = 0
        attempts_5xx = 0

        while True:
            self._respect_rate_limit()
            sent_at = self._clock()
            try:
                response = self._session.get(
                    url,
                    params=params,
                    timeout=self.timeout,
                )
            except requests.RequestException:
                self._sleep(2)
                continue
            finally:
                self._last_request_ts = sent_at

            self._on_request("odds")

            if response.status_code == 429:
                attempts_429 += 1
                self._on_rate_limit("odds")
                if attempts_429 > 5:
                    response.raise_for_status()
                self._sleep(60)
                continue

            if 500 <= response.status_code < 600:
                attempts_5xx += 1
                self._on_server_error("odds")
                if attempts_5xx > 5:
                    response.raise_for_status()
                self._sleep(min(2 ** attempts_5xx, 64))
                continue

            response.raise_for_status()
            if response.content:
                return response.json()
            return None

    def list_historical_events_by_date(self, snapshot_iso: str) -> List[Dict[str, Any]]:
        params = {"apiKey": self.api_key, "date": snapshot_iso}
        path = f"/historical/sports/{self.sport_key}/events"
        data = self._request(path, params=params)
        if isinstance(data, list):
            return data
        return []

    def historical_event_odds(
        self,
        event_id: str,
        snapshot_iso: str,
        markets: Iterable[str],
        region: str,
    ) -> List[Dict[str, Any]]:
        params = {
            "apiKey": self.api_key,
            "regions": region,
            "markets": ",".join(sorted(set(markets))),
            "date": snapshot_iso,
        }
        path = f"/historical/sports/{self.sport_key}/events/{event_id}/odds"
        data = self._request(path, params=params)
        if isinstance(data, list):
            return data
        return []

__all__ = ["TheOddsApiClient"]

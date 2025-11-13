"""SportsGameOdds API client with rate limiting and retry logic."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Dict, Iterable, List, Optional

import requests


RateLimitCallback = Callable[[str], None]


@dataclass(slots=True)
class _RetryPolicy:
    max_429: int = 5
    max_5xx: int = 5
    max_network: int = 3


class SportsGameOddsClient:
    """Minimal client for the SportsGameOdds REST API."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        timeout: float = 30.0,
        reqs_per_min: int = 10,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
        on_rate_limit: RateLimitCallback | None = None,
        on_server_error: RateLimitCallback | None = None,
        on_request: RateLimitCallback | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        if not base_url:
            raise ValueError("base_url is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": api_key,
                "Accept": "application/json",
                "User-Agent": "nba-best-bet/props-ingest",
            }
        )
        self._min_interval = 60.0 / max(reqs_per_min, 1)
        self._clock = clock or time.monotonic
        self._sleep = sleeper or time.sleep
        self._last_request_ts = 0.0
        self._retry_policy = _RetryPolicy()
        self._on_rate_limit = on_rate_limit or (lambda _: None)
        self._on_server_error = on_server_error or (lambda _: None)
        self._on_request = on_request or (lambda _: None)

    @property
    def last_request_ts(self) -> float:
        return self._last_request_ts

    def _respect_rate_limit(self) -> None:
        if self._min_interval <= 0:
            return
        now = self._clock()
        wait = self._min_interval - (now - self._last_request_ts)
        if wait > 0:
            self._sleep(wait)

    def _request(
        self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None
    ) -> Any:
        url = f"{self.base_url}{path}"
        attempts_429 = 0
        attempts_5xx = 0
        attempts_network = 0

        while True:
            self._respect_rate_limit()
            sent_at = self._clock()
            try:
                response = self._session.request(
                    method,
                    url,
                    params=params,
                    timeout=self.timeout,
                )
            except requests.RequestException:
                attempts_network += 1
                if attempts_network > self._retry_policy.max_network:
                    raise
                self._sleep(min(2 ** attempts_network, 5))
                continue
            finally:
                self._last_request_ts = sent_at

            self._on_request("sgo")

            if response.status_code == 429:
                attempts_429 += 1
                self._on_rate_limit("sgo")
                if attempts_429 > self._retry_policy.max_429:
                    response.raise_for_status()
                self._sleep(60)
                continue

            if 500 <= response.status_code < 600:
                attempts_5xx += 1
                self._on_server_error("sgo")
                if attempts_5xx > self._retry_policy.max_5xx:
                    response.raise_for_status()
                backoff = min(2 ** attempts_5xx, 64)
                self._sleep(backoff)
                continue

            response.raise_for_status()
            if response.content:
                return response.json()
            return None

    @staticmethod
    def _coerce_list(payload: Any, key: str) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        return []

    def list_events_by_date(self, game_date: date) -> List[Dict[str, Any]]:
        params = {"date": game_date.isoformat()}
        data = self._request("GET", "/nba/props/events", params=params)
        return self._coerce_list(data, "events")

    def get_event_props(
        self,
        event_id: str,
        markets: Iterable[str],
        region: str,
        *,
        include_alt_lines: bool = False,
    ) -> List[Dict[str, Any]]:
        params = {
            "markets": ",".join(sorted(set(markets))),
            "region": region,
        }
        if include_alt_lines:
            params["includeAltLines"] = "true"
        path = f"/nba/props/events/{event_id}/markets"
        data = self._request("GET", path, params=params)
        return self._coerce_list(data, "bookmakers")

    def get_account_usage(self) -> Dict[str, Any] | None:
        try:
            data = self._request("GET", "/account/usage")
        except requests.HTTPError:
            return None
        return data


__all__ = ["SportsGameOddsClient"]

"""Client for the NBA Injuries Reports API hosted on RapidAPI."""
from __future__ import annotations

import time
from datetime import date, datetime
from typing import List

import requests


class NBAInjuriesClient:
    """Thin wrapper around the RapidAPI injuries feed."""

    def __init__(
        self,
        api_key: str,
        host: str,
        base_url: str = "https://nba-injuries-reports.p.rapidapi.com",
        *,
        timeout: float = 30.0,
        max_attempts: int = 5,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.host = host
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-RapidAPI-Key": api_key,
                "X-RapidAPI-Host": host,
                "Accept": "application/json",
                "User-Agent": "nba-ingest-injuries/1.0",
            }
        )

    def _request(self, path: str) -> requests.Response:
        url = f"{self.base_url}{path}"
        attempt = 0
        delay = 0.5
        while True:
            try:
                response = self.session.get(url, timeout=self.timeout)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                attempt += 1
                if attempt >= self.max_attempts:
                    raise RuntimeError(
                        f"NBA injuries API request failed after {attempt} attempts: {exc}"
                    ) from exc
                time.sleep(delay)
                delay = min(delay * 2, 5.0)
                continue

            if response.status_code == 429:
                attempt += 1
                if attempt >= self.max_attempts:
                    raise RuntimeError("NBA injuries API rate limited repeatedly (429)")
                time.sleep(delay)
                delay = min(delay * 2, 5.0)
                continue

            return response

    def get_injuries_for_date(self, target_date: date | datetime | str) -> List[dict]:
        """Fetch the injuries report for the specified date."""

        if isinstance(target_date, (date, datetime)):
            day_str = target_date.strftime("%Y-%m-%d")
        else:
            day_str = str(target_date)
        response = self._request(f"/injuries/nba/{day_str}")
        if response.status_code != 200:
            raise RuntimeError(
                f"NBA injuries API returned {response.status_code}: {response.text}"
            )

        data = response.json()
        if not isinstance(data, list):
            raise RuntimeError("Unexpected injuries payload: expected a list of reports")
        return data


__all__ = ["NBAInjuriesClient"]

"""Client for interacting with the Ball Don't Lie NBA API."""
from __future__ import annotations

import time
from typing import Dict, Iterable, Iterator, List, Optional, Sequence

import requests


class BallDontLieClient:
    BASE_URL = "https://api.balldontlie.io"

    def __init__(self, api_key: str, base_url: Optional[str] = None, *, timeout: float = 30.0) -> None:
        self.api_key = api_key
        self.base_url = base_url or self.BASE_URL
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": api_key,
            "Accept": "application/json",
            "User-Agent": "nba-ingest/1.0",
        })

    def _request(self, method: str, path: str, params: Optional[Dict[str, object]] = None) -> Dict[str, object]:
        url = f"{self.base_url}{path}"
        backoff = 0.3
        while True:
            response = self.session.request(method, url, params=params, timeout=self.timeout)
            if response.status_code == 429:
                time.sleep(backoff)
                backoff = min(backoff * 2, 5.0)
                continue
            if response.status_code != 200:
                raise RuntimeError(
                    f"BallDontLie API request failed ({response.status_code}): {response.text}"
                )
            return response.json()

    def _paginate(self, path: str, params: Optional[Dict[str, object]] = None) -> Iterator[Dict[str, object]]:
        params = dict(params or {})
        if "per_page" not in params:
            params["per_page"] = 100
        cursor: Optional[str] = None
        while True:
            if cursor:
                params["cursor"] = cursor
            data = self._request("GET", path, params=params)
            items = data.get("data") if isinstance(data, dict) else None
            if isinstance(items, list):
                for item in items:
                    yield item
            elif items:
                # Some endpoints may return a single object instead of a list.
                yield items
            meta = data.get("meta") if isinstance(data, dict) else None
            cursor = meta.get("next_cursor") if isinstance(meta, dict) else None
            if not cursor:
                break
            time.sleep(0.25)

    def list_teams(self) -> List[Dict[str, object]]:
        params: Dict[str, object] = {"per_page": 100}
        teams = list(self._paginate("/v1/teams", params))
        return teams

    def list_games_for_seasons(
        self, seasons: Iterable[int], postseason: Optional[bool] = None
    ) -> Iterator[Dict[str, object]]:
        params: Dict[str, object] = {"per_page": 100}
        for season in seasons:
            params.setdefault("seasons[]", [])
            if isinstance(params["seasons[]"], list):
                params["seasons[]"].append(season)
        if postseason is not None:
            params["postseason"] = str(postseason).lower()
        yield from self._paginate("/v1/games", params)

    def list_advanced_stats_for_seasons(
        self, seasons: Iterable[int], postseason: Optional[bool] = None
    ) -> Iterator[Dict[str, object]]:
        params: Dict[str, object] = {"per_page": 100}
        for season in seasons:
            params.setdefault("seasons[]", [])
            if isinstance(params["seasons[]"], list):
                params["seasons[]"].append(season)
        if postseason is not None:
            params["postseason"] = str(postseason).lower()
        yield from self._paginate("/v1/stats/advanced", params)

    def list_odds_by_date(self, date: str) -> List[Dict[str, object]]:
        """Retrieve odds for the specified date (YYYY-MM-DD)."""
        params: Dict[str, object] = {"dates[]": date, "per_page": 100}
        return list(self._paginate("/v2/odds", params))

    def list_odds_by_game_ids(self, game_ids: Sequence[int]) -> List[Dict[str, object]]:
        """Retrieve odds for the specified collection of game IDs."""
        params: Dict[str, object] = {"per_page": 100}
        if game_ids:
            params["game_ids[]"] = list(game_ids)
        return list(self._paginate("/v2/odds", params))


__all__ = ["BallDontLieClient"]

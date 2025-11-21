import os
import argparse
import csv
import requests
from datetime import datetime, timedelta

API_BASE = "https://api.the-odds-api.com"


def get_api_key(cli_key: str | None) -> str:
    key = cli_key or os.getenv("ODDS_API_KEY")
    if not key:
        raise SystemExit(
            "ODDS_API_KEY environment variable is required, "
            "or pass --api-key on the command line."
        )
    return key


def discover_events_for_day(api_key: str, game_date: str, snapshot_iso: str) -> list[dict]:
    """
    Use /v4/historical/sports/basketball_nba/events to get all events
    for a given calendar date (UTC).
    """
    day_start = f"{game_date}T00:00:00Z"
    # use next day's midnight as upper bound
    next_day = (datetime.fromisoformat(game_date) + timedelta(days=1)).date().isoformat()
    day_end = f"{next_day}T00:00:00Z"

    url = f"{API_BASE}/v4/historical/sports/basketball_nba/events"
    params = {
        "apiKey": api_key,
        "dateFormat": "iso",
        "date": snapshot_iso,          # snapshot timestamp
        "commenceTimeFrom": day_start, # filter by game date
        "commenceTimeTo": day_end,
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # The endpoint is documented as "returns a list of events" – be defensive in case
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # if they ever wrap it, try common keys
        return data.get("events") or data.get("data") or []
    return []


def fetch_event_player_props(
    api_key: str,
    event_id: str,
    snapshot_iso: str,
    regions: str,
    markets: str,
    odds_format: str = "american",
) -> dict | list | None:
    """
    Use /v4/historical/sports/basketball_nba/events/{eventId}/odds
    with *player prop* markets only.
    """
    url = f"{API_BASE}/v4/historical/sports/basketball_nba/events/{event_id}/odds"
    params = {
        "apiKey": api_key,
        "regions": regions,
        "markets": markets,
        "dateFormat": "iso",
        "oddsFormat": odds_format,
        "date": snapshot_iso,
    }
    resp = requests.get(url, params=params, timeout=30)
    if resp.status_code == 404:
        # event may have “expired” at that snapshot
        return None
    resp.raise_for_status()
    return resp.json()


def flatten_to_rows(
    snapshot_iso: str,
    event: dict,
    odds_payload: dict | list,
    allowed_markets: set[str],
):
    """
    Flatten the event-odds JSON into CSV rows.
    Columns are chosen to help you design the DB schema later.
    """
    event_id = event.get("id")
    commence_time = event.get("commence_time")
    home_team = event.get("home_team")
    away_team = event.get("away_team")
    sport_key = event.get("sport_key")

    # payload shape can be either:
    #   { "bookmakers": [...] }
    # or directly [ {bookmaker}, ... ]
    if isinstance(odds_payload, dict):
        bookmakers = odds_payload.get("bookmakers") or odds_payload.get("data") or []
    else:
        bookmakers = odds_payload

    for bm in bookmakers or []:
        bm_key = bm.get("key")
        bm_title = bm.get("title")
        bm_last_update = bm.get("last_update")

        for market in bm.get("markets", []):
            market_key = market.get("key")
            if market_key not in allowed_markets:
                continue

            for outcome in market.get("outcomes", []):
                # The Odds API typically uses name/price/point; player props may also
                # have description or other fields – include them so you can inspect.
                yield {
                    "snapshot_iso": snapshot_iso,
                    "sport_key": sport_key,
                    "event_id": event_id,
                    "commence_time": commence_time,
                    "home_team": home_team,
                    "away_team": away_team,
                    "bookmaker_key": bm_key,
                    "bookmaker_title": bm_title,
                    "bookmaker_last_update": bm_last_update,
                    "market_key": market_key,
                    "outcome_name": outcome.get("name"),
                    "outcome_description": outcome.get("description"),
                    "outcome_price": outcome.get("price"),
                    "outcome_point": outcome.get("point"),
                }


def main():
    parser = argparse.ArgumentParser(
        description="Dump The Odds API NBA *player prop* snapshot to CSV"
    )
    parser.add_argument(
        "--date",
        required=True,
        help="Game date in YYYY-MM-DD (UTC), e.g. 2023-10-25",
    )
    parser.add_argument(
        "--snapshot",
        help=(
            "Snapshot timestamp in ISO8601 (UTC). "
            "Defaults to DATE + 'T23:59:00Z'."
        ),
    )
    parser.add_argument(
        "--markets",
        default="player_points,player_rebounds,player_assists",
        help="Comma-separated player prop markets (e.g. player_points,player_rebounds,player_assists,player_threes)",
    )
    parser.add_argument(
        "--regions",
        default="us",
        help="Comma-separated regions, default 'us'",
    )
    parser.add_argument(
        "--output",
        help="Output CSV path. Default: odds_player_props_<DATE>.csv",
    )
    parser.add_argument(
        "--api-key",
        help="Override ODDS_API_KEY env var",
    )

    args = parser.parse_args()
    api_key = get_api_key(args.api_key)

    game_date = args.date
    snapshot_iso = args.snapshot or f"{game_date}T23:59:00Z"
    markets_str = args.markets
    allowed_markets = {m.strip() for m in markets_str.split(",") if m.strip()}
    regions = args.regions

    output = args.output or f"odds_player_props_{game_date.replace('-', '')}.csv"

    print(f"[INFO] Using snapshot={snapshot_iso}, game_date={game_date}")
    print(f"[INFO] Markets={allowed_markets}, regions={regions}")
    print(f"[INFO] Output CSV: {output}")

    events = discover_events_for_day(api_key, game_date, snapshot_iso)
    print(f"[INFO] Found {len(events)} events for {game_date} in historical snapshot")

    fieldnames = [
        "snapshot_iso",
        "sport_key",
        "event_id",
        "commence_time",
        "home_team",
        "away_team",
        "bookmaker_key",
        "bookmaker_title",
        "bookmaker_last_update",
        "market_key",
        "outcome_name",
        "outcome_description",
        "outcome_price",
        "outcome_point",
    ]

    rows_written = 0
    with open(output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for event in events:
            event_id = event.get("id")
            if not event_id:
                continue

            try:
                odds_payload = fetch_event_player_props(
                    api_key=api_key,
                    event_id=event_id,
                    snapshot_iso=snapshot_iso,
                    regions=regions,
                    markets=markets_str,
                )
            except requests.HTTPError as e:
                print(f"[WARN] Event {event_id}: HTTP error {e}")
                continue
            except requests.RequestException as e:
                print(f"[WARN] Event {event_id}: request error {e}")
                continue

            if not odds_payload:
                # 404 or empty
                continue

            for row in flatten_to_rows(snapshot_iso, event, odds_payload, allowed_markets):
                writer.writerow(row)
                rows_written += 1

    print(f"[INFO] Wrote {rows_written} rows to {output}")


if __name__ == "__main__":
    main()

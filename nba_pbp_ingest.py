from datetime import datetime, timedelta
import random
import time
from pathlib import Path

import pandas as pd
from nba_api.stats.endpoints import PlayByPlayV2, ScoreboardV2


START_DATE = datetime(2021, 10, 19).date()
END_DATE = datetime(2025, 11, 11).date()
OUTPUT_DIR = Path("data/pbp")
BASE_DELAY_SECONDS = 1.5
JITTER_MIN = 0.3
JITTER_MAX = 0.9
MAX_RETRIES = 3

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def rate_limit_sleep() -> None:
    sleep_for = BASE_DELAY_SECONDS + random.uniform(JITTER_MIN, JITTER_MAX)
    print(f"Sleeping {sleep_for:.2f} seconds to respect rate limits...")
    time.sleep(sleep_for)


def with_retries(func, *args, **kwargs):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            print(f"Attempt {attempt} failed with error: {exc}")
            if attempt == MAX_RETRIES:
                print("Max retries reached, raising exception.")
                raise
            backoff = attempt * 10 + random.uniform(0, 5)
            print(f"Backing off for {backoff:.2f} seconds before retrying...")
            time.sleep(backoff)
    raise RuntimeError("Reached unexpected point in with_retries")


def get_game_ids_for_date(game_date: datetime.date) -> list[str]:
    game_date_str = game_date.strftime("%Y-%m-%d")
    print(f"Fetching scoreboard for date {game_date_str}")
    scoreboard = with_retries(lambda: ScoreboardV2(game_date=game_date_str))
    rate_limit_sleep()
    game_header_df = scoreboard.game_header.get_data_frame()
    if game_header_df.empty:
        print(f"No games scheduled for {game_date_str}")
        return []
    return game_header_df["GAME_ID"].astype(str).tolist()


def fetch_pbp_for_game(game_id: str) -> pd.DataFrame:
    print(f"Fetching play-by-play for game {game_id}")
    pbp = with_retries(lambda: PlayByPlayV2(game_id=game_id))
    rate_limit_sleep()
    frames = pbp.get_data_frames()
    if not frames:
        raise ValueError(f"Play-by-play response for game {game_id} did not return any frames.")
    df = frames[0].copy()
    df["GAME_ID"] = game_id
    return df


def save_pbp_dataframe(df: pd.DataFrame, game_id: str) -> None:
    file_path = OUTPUT_DIR / f"pbp_{game_id}.parquet"
    df.to_parquet(file_path, index=False)
    print(f"Saved play-by-play for game {game_id} to {file_path}")


def pbp_file_exists(game_id: str) -> bool:
    file_path = OUTPUT_DIR / f"pbp_{game_id}.parquet"
    exists = file_path.exists()
    if exists:
        print(f"File {file_path} already exists. Skipping game {game_id}.")
    return exists


def main() -> None:
    print(
        "Starting NBA play-by-play ingestion for date range "
        f"{START_DATE.isoformat()} to {END_DATE.isoformat()}"
    )
    current_date = START_DATE
    while current_date <= END_DATE:
        date_str = current_date.strftime("%Y-%m-%d")
        print(f"\nProcessing date {date_str}")
        game_ids = get_game_ids_for_date(current_date)
        if not game_ids:
            print(f"No games found for {date_str}")
        else:
            for game_id in game_ids:
                print(f"Processing game {game_id} on {date_str}")
                if pbp_file_exists(game_id):
                    continue
                df = fetch_pbp_for_game(game_id)
                save_pbp_dataframe(df, game_id)
        current_date += timedelta(days=1)
        print("Completed date processing; pausing for 3 seconds before next day.")
        time.sleep(3)


if __name__ == "__main__":
    main()

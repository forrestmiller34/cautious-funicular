"""Import SportsDataverse NBA play-by-play Parquet into raw Postgres tables."""
from __future__ import annotations

import argparse
import os
import re

import pandas as pd
from dotenv import load_dotenv
from .notifications import notify

from .db import create_db_engine


def log(message: str) -> None:
    """Lightweight stdout logging consistent with other ingest scripts."""

    print(f"[SDV_PBP_IMPORT] {message}", flush=True)


def _load_database_url() -> str:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required to import SDV PBP Parquet")
    return url


def import_sdv_pbp(parquet_path: str, table_name: str) -> None:
    """Load SDV PBP Parquet data into the specified raw table."""

    database_url = _load_database_url()
    log(f"Using DATABASE_URL={database_url}")
    engine = create_db_engine(database_url)

    log(f"Reading Parquet file from {parquet_path}")
    df = pd.read_parquet(parquet_path)
    log(f"Loaded DataFrame with shape {df.shape}")
    log(f"Columns: {list(df.columns)}")

    df.columns = [col.strip().lower() for col in df.columns]

    log(f"Writing DataFrame to table {table_name}")
    df.to_sql(
        name=table_name,
        con=engine,
        if_exists="fail",
        index=False,
        method="multi",
        chunksize=5000,
    )
    log("Import complete")


def _derive_table_name(parquet_path: str) -> str:
    filename = os.path.basename(parquet_path)
    match = re.search(r"(\d{4})", filename)
    year = match.group(1) if match else "unknown"
    return f"sdv_nba_pbp_{year}_raw"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import SDV NBA play-by-play Parquet into a raw table",
    )
    parser.add_argument(
        "--file",
        dest="file",
        required=True,
        help="Path to play_by_play_YYYY.parquet",
    )
    parser.add_argument(
        "--table",
        dest="table",
        required=False,
        help="Destination table name (defaults to sdv_nba_pbp_<year>_raw)",
    )
    args = parser.parse_args()

    table_name = args.table or _derive_table_name(args.file)

    notify(f"🟢 SDV PBP IMPORT started: {args.file} → {table_name}")
    try:
        import_sdv_pbp(args.file, table_name)
        notify(f"✅ SDV PBP IMPORT finished: {args.file} → {table_name}")
    except Exception as exc:
        notify(f"❌ SDV PBP IMPORT FAILED for {args.file}: {exc}")
        raise



if __name__ == "__main__":
    main()

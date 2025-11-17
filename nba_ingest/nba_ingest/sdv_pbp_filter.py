#!/usr/bin/env python
"""Filter SDV PBP parquet files by date range before import."""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd


def filter_parquet_by_date(
    input_path: str,
    output_path: str,
    start_date: date,
    end_date: date | None = None,
) -> tuple[int, int]:
    """
    Filter a parquet file to only include rows within a date range.

    Returns:
        Tuple of (original_rows, filtered_rows)
    """
    print(f"[SDV_FILTER] Reading {input_path}...")
    df = pd.read_parquet(input_path)
    original_rows = len(df)

    # Find the date column
    date_col = None
    for candidate in ["game_date", "date", "start_date"]:
        if candidate in df.columns:
            date_col = candidate
            break

    if not date_col:
        raise ValueError(
            f"Could not find date column. Available columns: {list(df.columns)}"
        )

    print(f"[SDV_FILTER] Using date column: {date_col}")

    # Convert to datetime if needed
    if not pd.api.types.is_datetime64_any_dtype(df[date_col]):
        df[date_col] = pd.to_datetime(df[date_col])

    # Filter by date range
    mask = df[date_col] >= pd.Timestamp(start_date)
    if end_date:
        mask &= df[date_col] <= pd.Timestamp(end_date)

    filtered_df = df[mask]
    filtered_rows = len(filtered_df)

    removed = original_rows - filtered_rows
    print(f"[SDV_FILTER] Original rows: {original_rows:,}")
    print(f"[SDV_FILTER] After filtering (>= {start_date}): {filtered_rows:,}")
    print(f"[SDV_FILTER] Removed: {removed:,} rows ({removed/original_rows*100:.1f}%)")

    # Show date range of filtered data
    if filtered_rows > 0:
        min_date = filtered_df[date_col].min()
        max_date = filtered_df[date_col].max()
        print(f"[SDV_FILTER] Date range in output: {min_date.date()} to {max_date.date()}")

    # Save filtered data
    print(f"[SDV_FILTER] Writing to {output_path}...")
    filtered_df.to_parquet(output_path, index=False)

    return original_rows, filtered_rows


def filter_multiple_files(
    input_dir: str,
    output_dir: str,
    start_date: date,
    end_date: date | None = None,
    pattern: str = "*.parquet",
) -> None:
    """Filter all parquet files in a directory."""
    input_path = Path(input_dir)
    output_path = Path(output_dir)

    if not input_path.exists():
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")

    output_path.mkdir(parents=True, exist_ok=True)

    files = list(input_path.glob(pattern))
    if not files:
        print(f"[SDV_FILTER] No files matching '{pattern}' in {input_dir}")
        return

    print(f"[SDV_FILTER] Found {len(files)} file(s) to filter")

    total_original = 0
    total_filtered = 0

    for file_path in sorted(files):
        output_file = output_path / file_path.name
        orig, filt = filter_parquet_by_date(
            str(file_path), str(output_file), start_date, end_date
        )
        total_original += orig
        total_filtered += filt
        print()

    print(f"[SDV_FILTER] Total: {total_filtered:,} / {total_original:,} rows kept")


def inspect_parquet_dates(input_path: str) -> None:
    """Show the date range of a parquet file."""
    print(f"[SDV_FILTER] Inspecting {input_path}...")
    df = pd.read_parquet(input_path)

    # Find the date column
    date_col = None
    for candidate in ["game_date", "date", "start_date"]:
        if candidate in df.columns:
            date_col = candidate
            break

    if not date_col:
        print(f"  ERROR: Could not find date column. Available: {list(df.columns)[:20]}...")
        return

    # Convert to datetime if needed
    if not pd.api.types.is_datetime64_any_dtype(df[date_col]):
        df[date_col] = pd.to_datetime(df[date_col])

    total_rows = len(df)
    min_date = df[date_col].min()
    max_date = df[date_col].max()
    unique_dates = df[date_col].dt.date.nunique()

    print(f"  Total rows: {total_rows:,}")
    print(f"  Date column: {date_col}")
    print(f"  First game date: {min_date.date()}")
    print(f"  Last game date: {max_date.date()}")
    print(f"  Unique game dates: {unique_dates}")

    # Show breakdown by month
    df["_month"] = df[date_col].dt.to_period("M")
    monthly = df.groupby("_month").size()
    print(f"\n  Monthly breakdown:")
    for month, count in monthly.items():
        print(f"    {month}: {count:,} rows")
    print()


def inspect_multiple_files(input_dir: str, pattern: str = "*.parquet") -> None:
    """Inspect all parquet files in a directory."""
    input_path = Path(input_dir)

    if not input_path.exists():
        raise FileNotFoundError(f"Directory does not exist: {input_dir}")

    files = list(input_path.glob(pattern))
    if not files:
        print(f"[SDV_FILTER] No files matching '{pattern}' in {input_dir}")
        return

    print(f"[SDV_FILTER] Found {len(files)} file(s) to inspect\n")

    for file_path in sorted(files):
        inspect_parquet_dates(str(file_path))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Filter SDV PBP parquet files by date range"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Inspect command
    inspect_parser = subparsers.add_parser(
        "inspect", help="Show date ranges in parquet file(s)"
    )
    inspect_parser.add_argument(
        "input",
        help="Input parquet file or directory to inspect",
    )
    inspect_parser.add_argument(
        "--pattern",
        type=str,
        default="*.parquet",
        help="File pattern for directory mode (default: *.parquet)",
    )

    # Filter command
    filter_parser = subparsers.add_parser(
        "filter", help="Filter parquet file(s) by date range"
    )
    filter_parser.add_argument(
        "input",
        help="Input parquet file or directory",
    )
    filter_parser.add_argument(
        "output",
        help="Output parquet file or directory",
    )
    filter_parser.add_argument(
        "--start-date",
        type=str,
        required=True,
        help="Start date (inclusive) in YYYY-MM-DD format",
    )
    filter_parser.add_argument(
        "--end-date",
        type=str,
        required=True,
        help="End date (inclusive) in YYYY-MM-DD format",
    )
    filter_parser.add_argument(
        "--pattern",
        type=str,
        default="*.parquet",
        help="File pattern for directory mode (default: *.parquet)",
    )

    args = parser.parse_args()

    if args.command == "inspect":
        input_path = Path(args.input)
        if input_path.is_file():
            inspect_parquet_dates(args.input)
        elif input_path.is_dir():
            inspect_multiple_files(args.input, args.pattern)
        else:
            raise FileNotFoundError(f"Input path does not exist: {args.input}")

    elif args.command == "filter":
        start_date = date.fromisoformat(args.start_date)
        end_date = date.fromisoformat(args.end_date)

        input_path = Path(args.input)

        if input_path.is_file():
            filter_parquet_by_date(args.input, args.output, start_date, end_date)
        elif input_path.is_dir():
            filter_multiple_files(args.input, args.output, start_date, end_date, args.pattern)
        else:
            raise FileNotFoundError(f"Input path does not exist: {args.input}")


if __name__ == "__main__":
    main()

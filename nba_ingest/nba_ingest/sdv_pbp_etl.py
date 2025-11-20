"""ETL helpers for SportsDataverse NBA play-by-play data."""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from typing import Mapping, Sequence

from dotenv import load_dotenv
from sqlalchemy import MetaData, Table, func, inspect, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .db import create_db_engine, create_session_factory, get_session
from .models import Game, PlayByPlayEvent, Player, SdvGameMap, Team
from .normalization import canonicalize_player_name

# Some SDV team codes differ from our Team.abbrev values.
# Map SDV's short/alt codes to the abbrevs used in our `teams` table.
TEAM_ABBREV_ALIASES: dict[str, str] = {
    "GS": "GSW",   # Golden State Warriors
    "SA": "SAS",   # San Antonio Spurs
    "NO": "NOP",   # New Orleans Pelicans
    "NY": "NYK",   # New York Knicks
    "WSH": "WAS",  # Washington Wizards
    "UTAH": "UTA", # Utah Jazz
}

# SDV codes for All-Star / non-NBA teams that don't exist in our `teams` table.
# We can safely ignore these for betting / model purposes.
NON_NBA_TEAM_CODES: set[str] = {
    "CAN",  # Canada / exhibition
    "KEN",  # Kentucky / college / exhibition
    "LEB",  # Team LeBron
    "GIA",  # Team Giannis
    "DUR",  # Team Durant
    "SHQ",  # Team Shaq
    "CHK",  # Team Chuck
    "EAST", # All-Star East
    "WEST", # All-Star West
}


def log(message: str) -> None:
    """Lightweight stdout logging consistent with other ingest scripts."""

    print(f"[SDV_PBP] {message}", flush=True)


def _load_database_url() -> str:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required to run SDV PBP ETL")
    return url


@dataclass(slots=True)
class RawTableColumns:
    """Column names discovered for an SDV raw PBP table."""

    sdv_game_id: str
    game_date: str
    internal_game_id: str
    season: str | None
    home_team: str | None
    away_team: str | None
    home_team_abbrev: str | None
    away_team_abbrev: str | None
    event_num: str
    period: str | None
    clock: str | None
    description: str | None
    event_type: str | None
    team_abbrev: str | None
    team_id: str | None
    player1_id: str | None
    player2_id: str | None
    player3_id: str | None
    home_score: str | None
    away_score: str | None


COLUMN_CANDIDATES: dict[str, Sequence[str]] = {
    "sdv_game_id": ("game_id", "game_uid", "game_pk", "id"),
    "game_date": ("game_date", "date", "start_date"),
    "season": ("season", "season_year"),
    "home_team": ("home_team_name", "home_team", "home"),
    "away_team": ("away_team_name", "away_team", "away"),
    "home_team_abbrev": (
        "home_team_abbreviation",
        "home_team_abbrev",
        "home_team_short",
    ),
    "away_team_abbrev": (
        "away_team_abbreviation",
        "away_team_abbrev",
        "away_team_short",
    ),
    "event_num": (
        "event_num",
        "event_number",
        "event_id",
        "play_id",
        "game_play_number",
        "sequence_number",
    ),
    "period": ("period", "period_number"),
    "clock": ("clock_display_value", "clock", "period_time_remaining"),
    "description": ("description", "text", "play_description"),
    "event_type": ("event_type", "event_action_type"),
    "team_abbrev": ("team_abbreviation", "team_abbrev", "team"),
    "team_id": ("team_id", "team_uid"),
    "player1_id": ("player1_id", "participant_id"),
    "player2_id": ("player2_id",),
    "player3_id": ("player3_id",),
    "home_score": ("home_score", "home_team_score"),
    "away_score": ("away_score", "away_team_score"),
}


def _find_column(available: set[str], candidates: Sequence[str]) -> str | None:
    for cand in candidates:
        if cand.lower() in available:
            return cand
    return None


def detect_columns(raw_table: Table) -> RawTableColumns:
    """Infer key column names for the provided raw table."""

    available = {col.name.lower() for col in raw_table.columns}

    def pick(key: str, required: bool = False) -> str | None:
        found = _find_column(available, COLUMN_CANDIDATES.get(key, ()))
        if not found and required:
            raise RuntimeError(f"Could not find required column for {key}")
        return found

    # Prefer internal_game_id, fall back to game_id if internal_game_id not present
    if "internal_game_id" in available:
        internal_game_id = "internal_game_id"
    elif "game_id" in available:
        # Check if game_id is the SDV identifier or the internal mapping
        sdv_game_id_candidate = pick("sdv_game_id", required=True)
        if sdv_game_id_candidate == "game_id":
            # game_id is the SDV identifier, we need to add internal_game_id column
            raise RuntimeError(
                "Raw table has 'game_id' as SDV identifier but no 'internal_game_id' column. "
                "Please add the 'internal_game_id' column to the raw table:\n"
                "  ALTER TABLE <table_name> ADD COLUMN internal_game_id BIGINT;"
            )
        internal_game_id = "game_id"
    else:
        raise RuntimeError(
            "Raw table is missing the destination game_id or internal_game_id column; "
            "run migrations first or add the column manually:\n"
            "  ALTER TABLE <table_name> ADD COLUMN internal_game_id BIGINT;"
        )

    sdv_game_id = pick("sdv_game_id", required=True)
    if sdv_game_id == internal_game_id:
        raise RuntimeError(
            "The SDV game identifier column conflicts with the mapped game_id column. "
            "Ensure the raw SDV id and the mapped game id use distinct column names."
        )

    return RawTableColumns(
        sdv_game_id=sdv_game_id,
        game_date=pick("game_date", required=True),
        internal_game_id=internal_game_id,
        season=pick("season"),
        home_team=pick("home_team"),
        away_team=pick("away_team"),
        home_team_abbrev=pick("home_team_abbrev"),
        away_team_abbrev=pick("away_team_abbrev"),
        event_num=pick("event_num", required=True),
        period=pick("period"),
        clock=pick("clock"),
        description=pick("description"),
        event_type=pick("event_type"),
        team_abbrev=pick("team_abbrev"),
        team_id=pick("team_id"),
        player1_id=pick("player1_id"),
        player2_id=pick("player2_id"),
        player3_id=pick("player3_id"),
        home_score=pick("home_score"),
        away_score=pick("away_score"),
    )


def load_raw_table(engine, table_name: str) -> Table:
    metadata = MetaData()
    return Table(table_name, metadata, autoload_with=engine)


def inspect_raw_table(engine, table_name: str) -> None:
    inspector = inspect(engine)
    cols = inspector.get_columns(table_name)
    log(f"{table_name} columns: {[col['name'] for col in cols]}")


def populate_game_map(
    session: Session, raw_table: Table, columns: RawTableColumns, season: int | None
) -> int:
    selectables = [
        raw_table.c[columns.sdv_game_id].label("sdv_game_id"),
        func.min(raw_table.c[columns.game_date]).label("game_date"),
    ]
    if columns.home_team_abbrev:
        selectables.append(
            func.min(raw_table.c[columns.home_team_abbrev]).label("sdv_home_team")
        )
    elif columns.home_team:
        selectables.append(func.min(raw_table.c[columns.home_team]).label("sdv_home_team"))
    if columns.away_team_abbrev:
        selectables.append(
            func.min(raw_table.c[columns.away_team_abbrev]).label("sdv_away_team")
        )
    elif columns.away_team:
        selectables.append(func.min(raw_table.c[columns.away_team]).label("sdv_away_team"))

    stmt = select(*selectables).group_by(raw_table.c[columns.sdv_game_id])
    results = session.execute(stmt).all()

    inserted = 0
    for row in results:
        mapping = row._mapping
        payload = {
            "sdv_game_id": mapping["sdv_game_id"],
            "season": season,
            "game_date": mapping.get("game_date"),
            "sdv_home_team": mapping.get("sdv_home_team"),
            "sdv_away_team": mapping.get("sdv_away_team"),
        }
        stmt = insert(SdvGameMap).values(**payload)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_sdv_game_map_sdv_game_id",
            set_={
                "season": payload["season"],
                "game_date": payload["game_date"],
                "sdv_home_team": payload["sdv_home_team"],
                "sdv_away_team": payload["sdv_away_team"],
            },
        )
        session.execute(stmt)
        inserted += 1
    log(f"Populated {inserted} sdv_game_map rows from {raw_table.name}")
    return inserted


def _resolve_team_id(session: Session, team_code: str | None) -> int | None:
    """
    Resolve an SDV team code to our internal Team.id.

    Handles:
    - Empty / None values (returns None)
    - All-Star / non-NBA teams (returns None)
    - Short/alt codes via TEAM_ABBREV_ALIASES (e.g., GS -> GSW, UTAH -> UTA)
    - Duplicate rows in `teams` by always picking a single row (lowest id)
    """
    if not team_code:
        return None

    # Normalize and uppercase the input code
    team_code_str = str(team_code).strip().upper()
    if not team_code_str:
        return None

    # Skip obvious non-NBA / All-Star teams entirely
    if team_code_str in NON_NBA_TEAM_CODES:
        return None

    # Apply alias mapping (GS -> GSW, SA -> SAS, etc.)
    team_code_str = TEAM_ABBREV_ALIASES.get(team_code_str, team_code_str)

    # 1) Try to match by Team.abbrev (case-insensitive)
    abbrev_stmt = (
        select(Team.id)
        .where(func.upper(Team.abbrev) == team_code_str)
        .order_by(Team.id)
        .limit(1)  # ensure at most one row so scalar_one_or_none() can't blow up
    )
    team = session.execute(abbrev_stmt).scalar_one_or_none()
    if team is not None:
        return team

    # 2) Fallback: match by nba_team_id (also case-insensitive)
    nba_id_stmt = (
        select(Team.id)
        .where(func.upper(Team.nba_team_id) == team_code_str)
        .order_by(Team.id)
        .limit(1)
    )
    return session.execute(nba_id_stmt).scalar_one_or_none()

def match_game_map(session: Session) -> int:
    from datetime import timedelta

    mappings = session.execute(
        select(SdvGameMap).where(SdvGameMap.matched.is_(False))
    ).scalars()

    updated = 0
    for mapping in mappings:
        game_id = None

        # Strategy 1: Try to match by SDV game ID directly (ESPN game ID)
        # Check if it's stored in nba_game_id or provider_event_ids
        sdv_id_str = str(mapping.sdv_game_id)
        game_id = session.execute(
            select(Game.id).where(Game.nba_game_id == sdv_id_str)
        ).scalar_one_or_none()
        if game_id:
            log(f"Matched {mapping.sdv_game_id} via nba_game_id")
            session.execute(
                SdvGameMap.__table__.update()
                .where(SdvGameMap.id == mapping.id)
                .values(internal_game_id=game_id, matched=True)
            )
            updated += 1
            continue

        # Strategy 2: Match by date and teams
        home_id = _resolve_team_id(session, mapping.sdv_home_team)
        away_id = _resolve_team_id(session, mapping.sdv_away_team)
        if not (home_id and away_id and mapping.game_date):
            # Provide more specific diagnostic info
            missing_parts = []
            if not mapping.game_date:
                missing_parts.append("game_date")
            if not mapping.sdv_home_team:
                missing_parts.append("home_team_code")
            elif not home_id:
                missing_parts.append(f"home_team_id ('{mapping.sdv_home_team}' not found)")
            if not mapping.sdv_away_team:
                missing_parts.append("away_team_code")
            elif not away_id:
                missing_parts.append(f"away_team_id ('{mapping.sdv_away_team}' not found)")
            log(
                f"Skipping match for {mapping.sdv_game_id}: missing {', '.join(missing_parts)}"
            )
            continue

        # Try exact match first
        game_id = session.execute(
            select(Game.id)
            .where(
                Game.game_date == mapping.game_date,
                Game.home_team_id == home_id,
                Game.away_team_id == away_id,
            )
            .limit(2)
        ).scalar_one_or_none()

        # If no match, try with swapped home/away (in case of data inconsistency)
        if not game_id:
            game_id = session.execute(
                select(Game.id)
                .where(
                    Game.game_date == mapping.game_date,
                    Game.home_team_id == away_id,
                    Game.away_team_id == home_id,
                )
                .limit(2)
            ).scalar_one_or_none()
            if game_id:
                log(
                    f"Found match for {mapping.sdv_game_id} with swapped home/away teams"
                )

        # If still no match, try ±1 day (timezone issues)
        if not game_id:
            for day_offset in [-1, 1]:
                adjusted_date = mapping.game_date + timedelta(days=day_offset)
                game_id = session.execute(
                    select(Game.id)
                    .where(
                        Game.game_date == adjusted_date,
                        Game.home_team_id == home_id,
                        Game.away_team_id == away_id,
                    )
                    .limit(2)
                ).scalar_one_or_none()
                if game_id:
                    log(
                        f"Found match for {mapping.sdv_game_id} on {adjusted_date} "
                        f"(offset by {day_offset} day)"
                    )
                    break
                # Also check swapped teams with date offset
                game_id = session.execute(
                    select(Game.id)
                    .where(
                        Game.game_date == adjusted_date,
                        Game.home_team_id == away_id,
                        Game.away_team_id == home_id,
                    )
                    .limit(2)
                ).scalar_one_or_none()
                if game_id:
                    log(
                        f"Found match for {mapping.sdv_game_id} on {adjusted_date} "
                        f"with swapped teams (offset by {day_offset} day)"
                    )
                    break

        if not game_id:
            # Check if there are any games on that date for diagnostic purposes
            games_on_date = session.execute(
                select(Game.id, Game.home_team_id, Game.away_team_id)
                .where(Game.game_date == mapping.game_date)
            ).all()
            if games_on_date:
                # Check if these teams have any games at all
                home_games = session.execute(
                    select(Game.id, Game.game_date)
                    .where(
                        ((Game.home_team_id == home_id) | (Game.away_team_id == home_id))
                    )
                    .order_by(Game.game_date)
                    .limit(5)
                ).all()
                away_games = session.execute(
                    select(Game.id, Game.game_date)
                    .where(
                        ((Game.home_team_id == away_id) | (Game.away_team_id == away_id))
                    )
                    .order_by(Game.game_date)
                    .limit(5)
                ).all()
                log(
                    f"No unique game match for {mapping.sdv_game_id} on {mapping.game_date} "
                    f"({mapping.sdv_away_team}@{mapping.sdv_home_team}). "
                    f"Found {len(games_on_date)} games on that date but none match. "
                    f"Home team ({mapping.sdv_home_team}) has {len(home_games)} games in DB. "
                    f"Away team ({mapping.sdv_away_team}) has {len(away_games)} games in DB."
                )
            else:
                log(
                    f"No unique game match for {mapping.sdv_game_id} on {mapping.game_date} "
                    f"({mapping.sdv_away_team}@{mapping.sdv_home_team}). "
                    f"No games found on {mapping.game_date} at all."
                )
            continue
        session.execute(
            SdvGameMap.__table__.update()
            .where(SdvGameMap.id == mapping.id)
            .values(internal_game_id=game_id, matched=True)
        )
        updated += 1
    log(f"Matched {updated} sdv_game_map rows to internal games")
    return updated


def backfill_raw_game_ids(
    session: Session, raw_table: Table, columns: RawTableColumns
) -> int:
    subquery = (
        select(SdvGameMap.internal_game_id)
        .where(
            SdvGameMap.sdv_game_id == raw_table.c[columns.sdv_game_id],
            SdvGameMap.matched.is_(True),
        )
        .scalar_subquery()
    )
    stmt = (
        raw_table.update()
        .where(raw_table.c[columns.internal_game_id].is_(None))
        .values({columns.internal_game_id: subquery})
    )
    result = session.execute(stmt)
    rows = result.rowcount or 0
    log(f"Updated game_id for {rows} raw rows in {raw_table.name}")
    return rows


def _to_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _resolve_player(session: Session, player_id: str | int | None) -> int | None:
    if player_id is None:
        return None
    player_id_str = str(player_id)
    player = session.execute(
        select(Player.id).where(Player.nba_player_id == player_id_str)
    ).scalar_one_or_none()
    if player:
        return player
    stmt = (
        insert(Player)
        .values(
            full_name=player_id_str,
            canonical_name=canonicalize_player_name(player_id_str),
            nba_player_id=player_id_str,
        )
        .on_conflict_do_nothing(constraint="uq_players_nba_player_id")
        .returning(Player.id)
    )
    return session.execute(stmt).scalar_one_or_none()


def _row_value(row: Mapping[str, object], key: str | None):
    return row.get(key) if key else None


def etl_play_by_play(
    session: Session, raw_table: Table, columns: RawTableColumns, limit: int | None
) -> int:
    stmt = select(raw_table).where(raw_table.c[columns.internal_game_id].is_not(None))
    if limit:
        stmt = stmt.limit(limit)
    rows = session.execute(stmt).mappings()

    payloads: list[dict] = []
    for row in rows:
        event_num = _to_int(_row_value(row, columns.event_num))
        game_id = _to_int(_row_value(row, columns.internal_game_id))
        if event_num is None or game_id is None:
            continue
        payload = {
            "game_id": game_id,
            "event_num": event_num,
            "period": _to_int(_row_value(row, columns.period)),
            "clock": str(_row_value(row, columns.clock))
            if _row_value(row, columns.clock) is not None
            else None,
            "event_type": _row_value(row, columns.event_type),
            "description": _row_value(row, columns.description),
            "home_score": _to_int(_row_value(row, columns.home_score)),
            "away_score": _to_int(_row_value(row, columns.away_score)),
            "raw_json": dict(row),
        }
        team_code = _row_value(row, columns.team_abbrev)
        team_id = _row_value(row, columns.team_id)
        payload["team_id"] = (
            _resolve_team_id(session, str(team_id))
            if team_id
            else _resolve_team_id(session, str(team_code) if team_code else None)
        )

        payload["player1_id"] = _resolve_player(session, _row_value(row, columns.player1_id))
        payload["player2_id"] = _resolve_player(session, _row_value(row, columns.player2_id))
        payload["player3_id"] = _resolve_player(session, _row_value(row, columns.player3_id))

        payloads.append(payload)

    if not payloads:
        log("No play-by-play rows ready for insertion")
        return 0

    stmt = insert(PlayByPlayEvent).values(payloads)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_pbp_game_event",
        set_={
            "period": stmt.excluded.period,
            "clock": stmt.excluded.clock,
            "event_type": stmt.excluded.event_type,
            "description": stmt.excluded.description,
            "team_id": stmt.excluded.team_id,
            "player1_id": stmt.excluded.player1_id,
            "player2_id": stmt.excluded.player2_id,
            "player3_id": stmt.excluded.player3_id,
            "home_score": stmt.excluded.home_score,
            "away_score": stmt.excluded.away_score,
            "raw_json": stmt.excluded.raw_json,
        },
    )
    result = session.execute(stmt)
    rows = result.rowcount or len(payloads)
    log(f"Upserted {rows} play_by_play rows from {raw_table.name}")
    return rows


def diagnose_unmatched(session: Session, output_file: str | None = None) -> None:
    """Show diagnostic info for unmatched games in sdv_game_map."""
    import csv

    unmatched = session.execute(
        select(SdvGameMap).where(SdvGameMap.matched.is_(False))
    ).scalars().all()

    if not unmatched:
        log("All sdv_game_map entries are matched!")
        return

    log(f"Found {len(unmatched)} unmatched sdv_game_map entries:")

    # Prepare data for CSV
    rows = []
    for mapping in unmatched:
        home_id = _resolve_team_id(session, mapping.sdv_home_team)
        away_id = _resolve_team_id(session, mapping.sdv_away_team)

        row = {
            "sdv_game_id": mapping.sdv_game_id,
            "season": mapping.season,
            "game_date": mapping.game_date,
            "sdv_home_team": mapping.sdv_home_team,
            "sdv_away_team": mapping.sdv_away_team,
            "home_team_id": home_id,
            "away_team_id": away_id,
            "matchup": f"{mapping.sdv_away_team}@{mapping.sdv_home_team}",
        }
        rows.append(row)

        # Still print to console
        log(
            f"  {mapping.sdv_game_id}: {mapping.game_date} "
            f"{mapping.sdv_away_team}(id={away_id})@{mapping.sdv_home_team}(id={home_id})"
        )

    # Write to CSV if output file specified
    if output_file and rows:
        with open(output_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        log(f"Wrote {len(rows)} unmatched games to {output_file}")


def main() -> None:
    parser = argparse.ArgumentParser(description="SDV play-by-play ETL")
    parser.add_argument("--source-table", required=True, help="Raw SDV table name")
    parser.add_argument("--season", type=int, help="Season year for mapping rows")
    parser.add_argument("--limit", type=int, help="Optional limit for ETL")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("inspect", help="List columns for the raw SDV table")
    subparsers.add_parser("populate-map", help="Populate sdv_game_map from the raw table")
    subparsers.add_parser("match-map", help="Match sdv_game_map rows to games")

    diagnose_parser = subparsers.add_parser("diagnose", help="Show diagnostic info for unmatched games")
    diagnose_parser.add_argument("--output", help="Output CSV file path for unmatched games")

    subparsers.add_parser(
        "backfill-game-ids",
        help="Fill raw table game_id using sdv_game_map mappings",
    )
    subparsers.add_parser(
        "etl", help="Load mapped SDV rows into the play_by_play table"
    )

    args = parser.parse_args()
    database_url = _load_database_url()
    engine = create_db_engine(database_url)
    session_factory = create_session_factory(engine)
    raw_table = load_raw_table(engine, args.source_table)
    columns = detect_columns(raw_table)

    if args.command == "inspect":
        inspect_raw_table(engine, args.source_table)
        return

    with get_session(session_factory) as session:
        if args.command == "populate-map":
            populate_game_map(session, raw_table, columns, args.season)
        elif args.command == "match-map":
            match_game_map(session)
        elif args.command == "diagnose":
            diagnose_unmatched(session, args.output)
        elif args.command == "backfill-game-ids":
            backfill_raw_game_ids(session, raw_table, columns)
        elif args.command == "etl":
            etl_play_by_play(session, raw_table, columns, args.limit)
        else:
            parser.error("Unknown command")


if __name__ == "__main__":
    main()

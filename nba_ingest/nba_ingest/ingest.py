"""Command-line entry point for ingesting NBA data from Ball Don't Lie."""
from __future__ import annotations

import argparse
from datetime import datetime
from typing import Any, Dict, Iterable, Optional

from sqlalchemy import func, select

from .balldontlie_client import BallDontLieClient
from .config import load_settings
from .db import create_db_engine, create_session_factory, get_session
from .models import Base, NBAGame, NBAPlayerAdvancedStats, NBATeam


DATE_FORMAT = "%Y-%m-%d"


def _parse_date(date_str: str) -> datetime.date:
    return datetime.strptime(date_str, DATE_FORMAT).date()


def _parse_datetime(datetime_str: Optional[str]) -> Optional[datetime]:
    if not datetime_str:
        return None
    # Handle ISO strings with Z suffix
    if datetime_str.endswith("Z"):
        datetime_str = datetime_str.replace("Z", "+00:00")
    return datetime.fromisoformat(datetime_str)


def _upsert_team(session, team_data: Dict[str, Any]) -> None:
    team = NBATeam(
        id=team_data["id"],
        abbreviation=team_data.get("abbreviation"),
        full_name=team_data.get("full_name"),
        city=team_data.get("city"),
        conference=team_data.get("conference"),
        division=team_data.get("division"),
    )
    session.merge(team)


def _upsert_game(session, game_data: Dict[str, Any]) -> None:
    home_team = game_data.get("home_team", {})
    visitor_team = game_data.get("visitor_team", {})
    game = NBAGame(
        id=game_data["id"],
        season=game_data["season"],
        date=_parse_date(game_data["date"]),
        datetime=_parse_datetime(game_data.get("datetime")),
        status=game_data.get("status"),
        period=game_data.get("period"),
        time=game_data.get("time"),
        postseason=game_data.get("postseason", False),
        home_team_id=home_team.get("id"),
        visitor_team_id=visitor_team.get("id"),
        home_team_score=game_data.get("home_team_score", 0),
        visitor_team_score=game_data.get("visitor_team_score", 0),
        home_q1=game_data.get("home_q1"),
        home_q2=game_data.get("home_q2"),
        home_q3=game_data.get("home_q3"),
        home_q4=game_data.get("home_q4"),
        home_ot1=game_data.get("home_ot1"),
        home_ot2=game_data.get("home_ot2"),
        home_ot3=game_data.get("home_ot3"),
        home_timeouts_remaining=game_data.get("home_timeouts_remaining"),
        home_in_bonus=game_data.get("home_in_bonus"),
        visitor_q1=game_data.get("visitor_q1"),
        visitor_q2=game_data.get("visitor_q2"),
        visitor_q3=game_data.get("visitor_q3"),
        visitor_q4=game_data.get("visitor_q4"),
        visitor_ot1=game_data.get("visitor_ot1"),
        visitor_ot2=game_data.get("visitor_ot2"),
        visitor_ot3=game_data.get("visitor_ot3"),
        visitor_timeouts_remaining=game_data.get("visitor_timeouts_remaining"),
        visitor_in_bonus=game_data.get("visitor_in_bonus"),
    )
    session.merge(game)


def _upsert_advanced_stats(session, stats_data: Dict[str, Any]) -> None:
    game = stats_data.get("game", {})
    team = stats_data.get("team", {})
    player = stats_data.get("player", {})
    stats = NBAPlayerAdvancedStats(
        id=stats_data["id"],
        game_id=game.get("id"),
        team_id=team.get("id"),
        player_id=player.get("id"),
        season=game.get("season"),
        postseason=game.get("postseason", False),
        pie=stats_data.get("pie"),
        pace=stats_data.get("pace"),
        assist_percentage=stats_data.get("assist_percentage"),
        assist_ratio=stats_data.get("assist_ratio"),
        assist_to_turnover=stats_data.get("assist_to_turnover"),
        defensive_rating=stats_data.get("defensive_rating"),
        defensive_rebound_percentage=stats_data.get("defensive_rebound_percentage"),
        effective_field_goal_percentage=stats_data.get("effective_field_goal_percentage"),
        net_rating=stats_data.get("net_rating"),
        offensive_rating=stats_data.get("offensive_rating"),
        offensive_rebound_percentage=stats_data.get("offensive_rebound_percentage"),
        rebound_percentage=stats_data.get("rebound_percentage"),
        true_shooting_percentage=stats_data.get("true_shooting_percentage"),
        turnover_ratio=stats_data.get("turnover_ratio"),
        usage_percentage=stats_data.get("usage_percentage"),
    )
    session.merge(stats)


def ingest_teams(client: BallDontLieClient, session) -> int:
    print("Ingesting teams…")
    teams = client.list_teams()
    for team in teams:
        _upsert_team(session, team)
    print(f"Inserted/updated {len(teams)} teams.")
    return len(teams)


def ingest_games(client: BallDontLieClient, session, seasons: Iterable[int]) -> int:
    total = 0
    for season in seasons:
        print(f"Ingesting games for season {season}…")
        season_count = 0
        for game in client.list_games_for_seasons([season]):
            _upsert_game(session, game)
            season_count += 1
            total += 1
            if season_count % 200 == 0:
                print(f"  Processed {season_count} games for season {season} so far…")
        print(f"  Season {season} total games processed: {season_count}")
    print(f"Total games processed: {total}")
    return total


def ingest_advanced_stats(client: BallDontLieClient, session, seasons: Iterable[int]) -> int:
    total = 0
    for season in seasons:
        print(f"Ingesting advanced stats for season {season}…")
        season_count = 0
        for stats in client.list_advanced_stats_for_seasons([season]):
            _upsert_advanced_stats(session, stats)
            season_count += 1
            total += 1
            if season_count % 500 == 0:
                print(f"  Processed {season_count} advanced stat rows for season {season} so far…")
        print(f"  Season {season} advanced stat rows processed: {season_count}")
    print(f"Total advanced stat rows processed: {total}")
    return total


def summarize_counts(session) -> Dict[str, int]:
    return {
        "teams": session.execute(select(func.count()).select_from(NBATeam)).scalar_one(),
        "games": session.execute(select(func.count()).select_from(NBAGame)).scalar_one(),
        "advanced_stats": session.execute(select(func.count()).select_from(NBAPlayerAdvancedStats)).scalar_one(),
    }


def main(argv: Optional[Iterable[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Ingest NBA data from Ball Don't Lie API")
    parser.parse_args(argv)

    settings = load_settings()
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)

    client = BallDontLieClient(settings.api_key)

    with get_session(session_factory) as session:
        ingest_teams(client, session)
        ingest_games(client, session, settings.seasons)
        ingest_advanced_stats(client, session, settings.seasons)
        session.flush()
        counts = summarize_counts(session)

    print("Ingestion complete.")
    print(
        "Database totals → Teams: {teams}, Games: {games}, Advanced Stats: {advanced_stats}".format(
            **counts
        )
    )


if __name__ == "__main__":
    main()

"""CLI to display ingestion progress across providers and seasons."""
from __future__ import annotations

import os

from dotenv import load_dotenv
from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError

from .db import create_db_engine, create_session_factory, get_session
from .models import Game, IngestionState, IngestionStatus


def _load_database_url() -> str:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required to show ingestion status")
    return url


def main() -> None:
    database_url = _load_database_url()
    engine = create_db_engine(database_url)
    session_factory = create_session_factory(engine)

    with get_session(session_factory) as session:
        print("=== ingestion_state progress ===")
        state_rows = session.execute(
            select(
                IngestionState.provider,
                IngestionState.last_successful_date,
            ).order_by(IngestionState.provider)
        )
        for provider, last_successful_date in state_rows:
            print(
                f"provider: {provider}, last_successful_date: {last_successful_date}"
            )

        print("\n=== games coverage ===")
        min_date, max_date, total_games = session.execute(
            select(func.min(Game.game_date), func.max(Game.game_date), func.count())
        ).one()
        print(f"earliest_game_date: {min_date}")
        print(f"latest_game_date:   {max_date}")
        print(f"total_games:        {total_games}")

        print("\n=== ingestion_status (season-level completion) ===")
        try:
            status_rows = session.execute(
                select(
                    IngestionStatus.source,
                    IngestionStatus.data_type,
                    IngestionStatus.season,
                    IngestionStatus.completed_at,
                ).order_by(
                    IngestionStatus.source,
                    IngestionStatus.data_type,
                    IngestionStatus.season,
                )
            )
        except ProgrammingError:
            session.rollback()
            print(
                "ingestion_status table not found; have you run the latest Alembic migrations?"
            )
        else:
            for source, data_type, season, completed_at in status_rows:
                print(
                    "source={source}, data_type={data_type}, season={season}, "
                    "completed_at={completed_at}".format(
                        source=source,
                        data_type=data_type,
                        season=season,
                        completed_at=completed_at,
                    )
                )


if __name__ == "__main__":
    main()

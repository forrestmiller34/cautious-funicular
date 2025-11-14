"""Integration-style tests for the unified odds ingestion helpers."""
from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker

from nba_ingest.models import Base, Game, OddsMarket
from nba_ingest.odds_helpers import get_or_create_league
from nba_ingest.odds_queries import (
    EventCount,
    OddsBreakdown,
    count_events_by_date,
    list_event_odds_breakdown,
)
from nba_ingest.unified_odds_ingest import (
    ingest_betsapi_team_odds,
    ingest_odds_api_player_props,
    ingest_sgo_player_props,
    summarize_stats,
)


class _FakeBetsApiClient:
    def list_events_for_date(self, target_date: date) -> list[dict]:
        if target_date != date(2021, 10, 19):
            return []
        return [
            {
                "id": "bets-evt-1",
                "home": "Los Angeles Lakers",
                "away": "Golden State Warriors",
                "time": "2021-10-19T23:00:00Z",
            }
        ]

    def fetch_event_odds(self, event_id: str) -> dict:
        return {
            "bookmakers": [
                {
                    "id": "fd",
                    "title": "FanDuel",
                    "markets": [
                        {
                            "key": "moneyline",
                            "outcomes": [
                                {"name": "Home", "price": -120},
                                {"name": "Away", "price": +105},
                            ],
                        },
                        {
                            "key": "spread",
                            "outcomes": [
                                {"name": "Home", "price": -110, "point": -3.5},
                                {"name": "Away", "price": -110, "point": 3.5},
                            ],
                        },
                        {
                            "key": "total",
                            "outcomes": [
                                {"name": "Over", "price": -105, "point": 225.5},
                                {"name": "Under", "price": -115, "point": 225.5},
                            ],
                        },
                    ],
                }
            ]
        }


class _FakeSgoClient:
    def fetch_events_for_date(self, target_date: date) -> list[dict]:
        if target_date != date(2021, 10, 19):
            return []
        return [
            {
                "id": "sgo-evt-1",
                "startTime": "2021-10-19T23:00:00Z",
                "homeTeam": {"name": "Los Angeles Lakers"},
                "awayTeam": {"name": "Golden State Warriors"},
                "bookmakers": [
                    {
                        "id": "dk",
                        "title": "DraftKings",
                        "markets": [
                            {
                                "category": "Player Points",
                                "outcomes": [
                                    {
                                        "participant": {
                                            "id": "p1",
                                            "name": "LeBron James",
                                            "team": "Los Angeles Lakers",
                                        },
                                        "type": "over",
                                        "line": 26.5,
                                        "price": -115,
                                    },
                                    {
                                        "participant": {
                                            "id": "p1",
                                            "name": "LeBron James",
                                            "team": "Los Angeles Lakers",
                                        },
                                        "type": "under",
                                        "line": 26.5,
                                        "price": -105,
                                    },
                                ],
                            }
                        ],
                    }
                ],
            }
        ]


class _FakeOddsApiClient:
    def list_events_for_date(self, target_date: date) -> list[dict]:
        if target_date != date(2023, 5, 3):
            return []
        return [
            {
                "id": "odds-evt-1",
                "home_team": "Los Angeles Lakers",
                "away_team": "Golden State Warriors",
                "commence_time": "2023-05-03T23:00:00Z",
            }
        ]

    def fetch_event_player_props(self, event_id: str, markets: tuple[str, ...]) -> dict:
        return {
            "bookmakers": [
                {
                    "key": "pointsbet",
                    "title": "PointsBet",
                    "markets": [
                        {
                            "key": "player_points",
                            "last_update": "2023-05-03T12:00:00Z",
                            "outcomes": [
                                {
                                    "description": "Stephen Curry",
                                    "name": "Over",
                                    "point": 28.5,
                                    "price": -110,
                                },
                                {
                                    "description": "Stephen Curry",
                                    "name": "Under",
                                    "point": 28.5,
                                    "price": -110,
                                },
                            ],
                        }
                    ],
                }
            ]
        }


def test_unified_pipeline_writes_markets_across_all_providers() -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

    with SessionLocal() as session:
        league = get_or_create_league(session, "NBA")
        session.commit()
        league_id = league.id

    bets_stats = ingest_betsapi_team_odds(
        session_factory=SessionLocal,
        league_id=league_id,
        client=_FakeBetsApiClient(),
        start_date=date(2021, 10, 19),
        end_date=date(2021, 10, 19),
    )

    sgo_stats = ingest_sgo_player_props(
        session_factory=SessionLocal,
        league_id=league_id,
        client=_FakeSgoClient(),
        start_date=date(2021, 10, 19),
        end_date=date(2021, 10, 19),
        markets=("player_points", "player_assists", "player_rebounds", "player_threes"),
    )

    odds_stats = ingest_odds_api_player_props(
        session_factory=SessionLocal,
        league_id=league_id,
        client=_FakeOddsApiClient(),
        start_date=date(2023, 5, 3),
        end_date=date(2023, 5, 3),
        markets=("player_points",),
    )

    with SessionLocal() as session:
        game_count = session.scalar(select(func.count()).select_from(Game))
        odds_rows = session.scalars(select(OddsMarket)).all()
        providers = {row.provider for row in odds_rows}

    assert game_count >= 2
    assert {"betsapi", "sportsgameodds", "the_odds_api"}.issubset(providers)
    assert len(odds_rows) >= 8

    counts = count_events_by_date(session, date(2021, 10, 19), date(2023, 5, 3))
    assert any(isinstance(entry, EventCount) for entry in counts)
    assert any(entry.total_events > 0 for entry in counts)

    breakdown = list_event_odds_breakdown(session, odds_rows[0].event_id)
    assert any(isinstance(item, OddsBreakdown) for item in breakdown)
    assert any(item.provider == "sportsgameodds" for item in breakdown)

    assert bets_stats.provider == "betsapi"
    assert sgo_stats.provider == "sportsgameodds"
    assert odds_stats.provider == "the_odds_api"
    assert "betsapi" in summarize_stats(bets_stats)
    assert bets_stats.odds_rows > 0


def test_betsapi_dry_run_skips_writes() -> None:
    SessionLocal = sessionmaker()
    stats = ingest_betsapi_team_odds(
        session_factory=SessionLocal,
        league_id=0,
        client=_FakeBetsApiClient(),
        start_date=date(2021, 10, 19),
        end_date=date(2021, 10, 19),
        dry_run=True,
    )
    assert stats.events_seen == 1
    assert stats.odds_rows > 0

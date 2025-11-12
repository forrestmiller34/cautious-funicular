"""Utilities for loading the trained model and predicting win probabilities."""
from __future__ import annotations

import argparse
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import joblib
import pandas as pd
from sqlalchemy import select
from sqlalchemy.engine import Engine

from .config import load_settings
from .db import create_db_engine
from .features import MODEL_FEATURE_COLUMNS, ROLLING_WINDOW_DEFAULT, build_matchup_features
from .models import NBAGame


def load_model(model_path: Path | str = Path("models/win_prob_model.pkl")):
    """Load the trained win probability model from disk."""
    path = Path(model_path)
    if not path.exists():
        raise FileNotFoundError(f"Model file not found at {path}")
    return joblib.load(path)


def _lookup_season(
    engine: Engine,
    home_team_id: int,
    visitor_team_id: int,
    game_date: date,
) -> int:
    with engine.connect() as conn:
        season_value = conn.execute(
            select(NBAGame.season)
            .where(NBAGame.home_team_id == home_team_id)
            .where(NBAGame.visitor_team_id == visitor_team_id)
            .where(NBAGame.date == game_date)
            .limit(1)
        ).scalar_one_or_none()
    if season_value is not None:
        return season_value
    # Approximate: NBA seasons begin in the fall of the stated year.
    return game_date.year if game_date.month >= 7 else game_date.year - 1


def build_single_matchup_features(
    engine: Engine,
    home_team_id: int,
    visitor_team_id: int,
    game_date: date,
    rolling_window: int = ROLLING_WINDOW_DEFAULT,
    season: Optional[int] = None,
) -> pd.DataFrame:
    """Construct a single-row feature frame for the specified matchup."""
    target_season = season or _lookup_season(engine, home_team_id, visitor_team_id, game_date)
    feature_df = build_matchup_features(
        engine,
        home_team_id=home_team_id,
        visitor_team_id=visitor_team_id,
        game_date=game_date,
        season=target_season,
        rolling_window=rolling_window,
    )
    return feature_df


def predict_home_win_probability(
    engine: Engine,
    home_team_id: int,
    visitor_team_id: int,
    game_date: date,
    model_path: Path | str = Path("models/win_prob_model.pkl"),
    rolling_window: int = ROLLING_WINDOW_DEFAULT,
) -> float:
    model = load_model(model_path)
    feature_row = build_single_matchup_features(
        engine,
        home_team_id=home_team_id,
        visitor_team_id=visitor_team_id,
        game_date=game_date,
        rolling_window=rolling_window,
    )
    probabilities = model.predict_proba(feature_row[MODEL_FEATURE_COLUMNS])[:, 1]
    return float(probabilities[0])


def main() -> None:
    parser = argparse.ArgumentParser(description="Predict home win probability for a matchup.")
    parser.add_argument("--home_team_id", type=int, required=True)
    parser.add_argument("--visitor_team_id", type=int, required=True)
    parser.add_argument(
        "--date",
        type=lambda value: datetime.strptime(value, "%Y-%m-%d").date(),
        required=True,
        help="Game date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--model-path",
        type=Path,
        default=Path("models/win_prob_model.pkl"),
        help="Path to the trained model file.",
    )
    parser.add_argument(
        "--rolling-window",
        type=int,
        default=ROLLING_WINDOW_DEFAULT,
        help="Number of past games to use for recent form features.",
    )
    args = parser.parse_args()

    settings = load_settings()
    engine = create_db_engine(settings.database_url)

    probability = predict_home_win_probability(
        engine,
        home_team_id=args.home_team_id,
        visitor_team_id=args.visitor_team_id,
        game_date=args.date,
        model_path=args.model_path,
        rolling_window=args.rolling_window,
    )
    print(f"Predicted home win probability: {probability:.3f}")


if __name__ == "__main__":
    main()

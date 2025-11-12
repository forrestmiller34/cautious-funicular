"""CLI for training the NBA home win probability model."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import joblib
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .config import load_settings
from .db import create_db_engine
from .features import (
    MODEL_FEATURE_COLUMNS,
    ROLLING_WINDOW_DEFAULT,
    get_training_dataframe,
)


def _split_train_test(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    seasons_sorted = sorted(df["season"].unique())
    if len(seasons_sorted) > 1:
        test_season = seasons_sorted[-1]
        train_df = df[df["season"] < test_season]
        test_df = df[df["season"] == test_season]
        if train_df.empty:
            raise ValueError("Not enough historical seasons to form a training set.")
        return train_df, test_df, test_season

    # Fallback to random split if only a single season is available.
    train_df, test_df = train_test_split(
        df,
        test_size=0.2,
        random_state=42,
        stratify=df["home_win"] if df["home_win"].nunique() > 1 else None,
    )
    test_season = seasons_sorted[0]
    return train_df, test_df, test_season


def _build_model() -> Pipeline:
    return Pipeline(
        steps=
        [
            ("scaler", StandardScaler()),
            (
                "classifier",
                LogisticRegression(max_iter=1000, solver="lbfgs"),
            ),
        ]
    )


def train_win_probability_model(
    database_url: str,
    seasons: Iterable[int],
    rolling_window: int,
    model_path: Path,
) -> None:
    engine = create_db_engine(database_url)

    print(f"Loading training data for seasons: {sorted(seasons)}")
    training_df = get_training_dataframe(engine, seasons=seasons, rolling_window=rolling_window)
    print(f"Constructed {len(training_df)} training rows")

    train_df, test_df, test_season = _split_train_test(training_df)

    X_train = train_df[MODEL_FEATURE_COLUMNS]
    y_train = train_df["home_win"]
    X_test = test_df[MODEL_FEATURE_COLUMNS]
    y_test = test_df["home_win"]

    model = _build_model()
    model.fit(X_train, y_train)

    test_probs = model.predict_proba(X_test)[:, 1]
    test_preds = (test_probs >= 0.5).astype(int)

    accuracy = accuracy_score(y_test, test_preds)
    try:
        roc_auc = roc_auc_score(y_test, test_probs)
    except ValueError:
        roc_auc = float("nan")
    brier = brier_score_loss(y_test, test_probs)

    print("Evaluation metrics (held-out season or split):")
    print(f"  Test season: {test_season}")
    print(f"  Accuracy: {accuracy:.3f}")
    print(f"  ROC AUC: {roc_auc:.3f}")
    print(f"  Brier score: {brier:.3f}")

    print("Sample predictions (probability -> actual result):")
    sample_count = min(5, len(test_df))
    for prob, actual in zip(test_probs[:sample_count], y_test.iloc[:sample_count]):
        outcome = "W" if actual == 1 else "L"
        print(f"  {prob:.3f} -> {outcome}")

    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, model_path)
    print(f"Saved model to {model_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the NBA home win probability model.")
    parser.add_argument(
        "--model-path",
        type=Path,
        default=Path("models/win_prob_model.pkl"),
        help="Path where the trained model will be saved.",
    )
    parser.add_argument(
        "--rolling-window",
        type=int,
        default=ROLLING_WINDOW_DEFAULT,
        help="Number of past games to use for recent form features.",
    )
    args = parser.parse_args()

    settings = load_settings()
    seasons = settings.seasons
    train_win_probability_model(
        database_url=settings.database_url,
        seasons=seasons,
        rolling_window=args.rolling_window,
        model_path=args.model_path,
    )


if __name__ == "__main__":
    main()

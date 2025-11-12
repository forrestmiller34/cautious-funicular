"""Feature engineering utilities for the win probability model."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

import pandas as pd
from sqlalchemy import Select, select
from sqlalchemy.engine import Engine

from .models import NBAGame, NBAPlayerAdvancedStats

ROLLING_WINDOW_DEFAULT = 10
MODEL_FEATURE_COLUMNS = [
    "diff_season_net_rating",
    "diff_season_off_rating",
    "diff_season_def_rating",
    "diff_season_pace",
    "diff_recent_point_diff",
    "diff_recent_net_rating",
    "home_rest_days",
    "away_rest_days",
]


def _build_games_statement(seasons: Optional[Iterable[int]] = None, max_season: Optional[int] = None) -> Select:
    stmt = (
        select(
            NBAGame.id,
            NBAGame.season,
            NBAGame.date,
            NBAGame.home_team_id,
            NBAGame.visitor_team_id,
            NBAGame.home_team_score,
            NBAGame.visitor_team_score,
            NBAGame.status,
            NBAGame.postseason,
        )
        .where(NBAGame.postseason.is_(False))
        .where(NBAGame.home_team_score.isnot(None))
        .where(NBAGame.visitor_team_score.isnot(None))
        .where(NBAGame.status.ilike("Final%"))
    )
    if seasons:
        stmt = stmt.where(NBAGame.season.in_(list(seasons)))
    if max_season is not None:
        stmt = stmt.where(NBAGame.season <= max_season)
    return stmt


def _build_adv_stats_statement(seasons: Optional[Iterable[int]] = None, max_season: Optional[int] = None) -> Select:
    stmt = (
        select(
            NBAPlayerAdvancedStats.id,
            NBAPlayerAdvancedStats.game_id,
            NBAPlayerAdvancedStats.team_id,
            NBAPlayerAdvancedStats.season,
            NBAPlayerAdvancedStats.postseason,
            NBAPlayerAdvancedStats.offensive_rating,
            NBAPlayerAdvancedStats.defensive_rating,
            NBAPlayerAdvancedStats.net_rating,
            NBAPlayerAdvancedStats.pace,
            NBAPlayerAdvancedStats.true_shooting_percentage,
            NBAPlayerAdvancedStats.usage_percentage,
        )
        .where(NBAPlayerAdvancedStats.postseason.is_(False))
    )
    if seasons:
        stmt = stmt.where(NBAPlayerAdvancedStats.season.in_(list(seasons)))
    if max_season is not None:
        stmt = stmt.where(NBAPlayerAdvancedStats.season <= max_season)
    return stmt


def _load_games(engine: Engine, seasons: Optional[Iterable[int]] = None, max_season: Optional[int] = None) -> pd.DataFrame:
    stmt = _build_games_statement(seasons=seasons, max_season=max_season)
    games = pd.read_sql(stmt, engine, parse_dates=["date"])
    if games.empty:
        return games
    games = games.sort_values(["date", "id"]).reset_index(drop=True)
    return games


def _load_adv_stats(engine: Engine, seasons: Optional[Iterable[int]] = None, max_season: Optional[int] = None) -> pd.DataFrame:
    stmt = _build_adv_stats_statement(seasons=seasons, max_season=max_season)
    adv = pd.read_sql(stmt, engine)
    return adv


def _prepare_team_games(games: pd.DataFrame, adv: pd.DataFrame) -> pd.DataFrame:
    if games.empty:
        return pd.DataFrame(
            columns=[
                "game_id",
                "team_id",
                "season",
                "date",
                "team_score",
                "opponent_score",
                "is_home",
                "game_offensive_rating",
                "game_defensive_rating",
                "game_net_rating",
                "game_pace",
                "game_true_shooting_percentage",
                "game_usage_percentage",
            ]
        )

    team_adv = pd.DataFrame(columns=["game_id", "team_id"])
    if not adv.empty:
        adv = adv[adv["game_id"].isin(games["id"])].copy()
        team_adv = (
            adv.groupby(["game_id", "team_id"], as_index=False)
            .agg(
                {
                    "offensive_rating": "mean",
                    "defensive_rating": "mean",
                    "net_rating": "mean",
                    "pace": "mean",
                    "true_shooting_percentage": "mean",
                    "usage_percentage": "mean",
                }
            )
            .rename(
                columns={
                    "offensive_rating": "game_offensive_rating",
                    "defensive_rating": "game_defensive_rating",
                    "net_rating": "game_net_rating",
                    "pace": "game_pace",
                    "true_shooting_percentage": "game_true_shooting_percentage",
                    "usage_percentage": "game_usage_percentage",
                }
            )
        )

    home = games[
        [
            "id",
            "season",
            "date",
            "home_team_id",
            "visitor_team_id",
            "home_team_score",
            "visitor_team_score",
        ]
    ].copy()
    home.rename(
        columns={
            "id": "game_id",
            "home_team_id": "team_id",
            "visitor_team_id": "opponent_id",
            "home_team_score": "team_score",
            "visitor_team_score": "opponent_score",
        },
        inplace=True,
    )
    home["is_home"] = True

    away = games[
        [
            "id",
            "season",
            "date",
            "visitor_team_id",
            "home_team_id",
            "visitor_team_score",
            "home_team_score",
        ]
    ].copy()
    away.rename(
        columns={
            "id": "game_id",
            "visitor_team_id": "team_id",
            "home_team_id": "opponent_id",
            "visitor_team_score": "team_score",
            "home_team_score": "opponent_score",
        },
        inplace=True,
    )
    away["is_home"] = False

    team_games = pd.concat([home, away], ignore_index=True, sort=False)
    team_games = team_games.merge(team_adv, on=["game_id", "team_id"], how="left")
    return team_games


def _compute_team_features(team_games: pd.DataFrame, rolling_window: int) -> pd.DataFrame:
    if team_games.empty:
        return team_games.assign(
            point_diff=pd.Series(dtype="float64"),
            rest_days=pd.Series(dtype="float64"),
            recent_avg_point_diff=pd.Series(dtype="float64"),
            recent_avg_offensive_rating=pd.Series(dtype="float64"),
            recent_avg_defensive_rating=pd.Series(dtype="float64"),
            recent_avg_net_rating=pd.Series(dtype="float64"),
            season_avg_offensive_rating=pd.Series(dtype="float64"),
            season_avg_defensive_rating=pd.Series(dtype="float64"),
            season_avg_net_rating=pd.Series(dtype="float64"),
            season_avg_pace=pd.Series(dtype="float64"),
            season_avg_true_shooting=pd.Series(dtype="float64"),
            season_avg_usage=pd.Series(dtype="float64"),
        )

    df = team_games.sort_values(["team_id", "date", "game_id"]).reset_index(drop=True)
    df["point_diff"] = df["team_score"] - df["opponent_score"]
    df["rest_days"] = df.groupby("team_id")["date"].diff().dt.days

    rolling_map = {
        "point_diff": "recent_avg_point_diff",
        "game_offensive_rating": "recent_avg_offensive_rating",
        "game_defensive_rating": "recent_avg_defensive_rating",
        "game_net_rating": "recent_avg_net_rating",
    }
    for source_col, target_col in rolling_map.items():
        df[target_col] = (
            df.groupby("team_id")[source_col]
            .apply(lambda s: s.shift(1).rolling(window=rolling_window, min_periods=1).mean())
            .reset_index(level=0, drop=True)
        )

    season_map = {
        "game_offensive_rating": "season_avg_offensive_rating",
        "game_defensive_rating": "season_avg_defensive_rating",
        "game_net_rating": "season_avg_net_rating",
        "game_pace": "season_avg_pace",
        "game_true_shooting_percentage": "season_avg_true_shooting",
        "game_usage_percentage": "season_avg_usage",
    }
    for source_col, target_col in season_map.items():
        df[target_col] = (
            df.groupby(["season", "team_id"])[source_col]
            .apply(lambda s: s.shift(1).expanding(min_periods=1).mean())
            .reset_index(level=[0, 1], drop=True)
        )

    return df


def _merge_game_features(games: pd.DataFrame, team_features: pd.DataFrame) -> pd.DataFrame:
    if games.empty:
        return pd.DataFrame()

    feature_cols = [
        "game_id",
        "team_id",
        "recent_avg_point_diff",
        "recent_avg_offensive_rating",
        "recent_avg_defensive_rating",
        "recent_avg_net_rating",
        "season_avg_offensive_rating",
        "season_avg_defensive_rating",
        "season_avg_net_rating",
        "season_avg_pace",
        "season_avg_true_shooting",
        "season_avg_usage",
        "rest_days",
    ]
    team_features = team_features[feature_cols].copy()

    home_features = team_features.add_prefix("home_")
    home_features.rename(columns={"home_game_id": "game_id", "home_team_id": "team_id"}, inplace=True)
    games = games.merge(
        home_features,
        left_on=["id", "home_team_id"],
        right_on=["game_id", "team_id"],
        how="left",
    )
    games.drop(columns=["game_id", "team_id"], inplace=True)

    away_features = team_features.add_prefix("away_")
    away_features.rename(columns={"away_game_id": "game_id", "away_team_id": "team_id"}, inplace=True)
    games = games.merge(
        away_features,
        left_on=["id", "visitor_team_id"],
        right_on=["game_id", "team_id"],
        how="left",
    )
    games.drop(columns=["game_id", "team_id"], inplace=True)

    games["home_win"] = (games["home_team_score"] > games["visitor_team_score"]).astype(int)

    games["diff_season_net_rating"] = (
        games["home_season_avg_net_rating"] - games["away_season_avg_net_rating"]
    )
    games["diff_season_off_rating"] = (
        games["home_season_avg_offensive_rating"] - games["away_season_avg_offensive_rating"]
    )
    games["diff_season_def_rating"] = (
        games["home_season_avg_defensive_rating"] - games["away_season_avg_defensive_rating"]
    )
    games["diff_season_pace"] = games["home_season_avg_pace"] - games["away_season_avg_pace"]
    games["diff_recent_point_diff"] = (
        games["home_recent_avg_point_diff"] - games["away_recent_avg_point_diff"]
    )
    games["diff_recent_net_rating"] = (
        games["home_recent_avg_net_rating"] - games["away_recent_avg_net_rating"]
    )

    columns = [
        "season",
        "date",
        "home_team_id",
        "visitor_team_id",
        *MODEL_FEATURE_COLUMNS,
        "home_win",
    ]

    features = games[columns].copy()

    diff_cols = [
        "diff_season_net_rating",
        "diff_season_off_rating",
        "diff_season_def_rating",
        "diff_season_pace",
        "diff_recent_point_diff",
        "diff_recent_net_rating",
    ]
    features[diff_cols] = features[diff_cols].fillna(0.0)

    rest_cols = ["home_rest_days", "away_rest_days"]
    rest_medians = features[rest_cols].median().fillna(3.0)
    features[rest_cols] = features[rest_cols].fillna(rest_medians)

    features["home_win"] = features["home_win"].astype(int)
    features.sort_values(["season", "date", "home_team_id"], inplace=True)
    features.reset_index(drop=True, inplace=True)
    return features


def get_training_dataframe(
    engine: Engine,
    seasons: Iterable[int],
    rolling_window: int = ROLLING_WINDOW_DEFAULT,
) -> pd.DataFrame:
    games = _load_games(engine, seasons=seasons)
    if games.empty:
        raise ValueError("No completed regular-season games found for the requested seasons.")

    adv = _load_adv_stats(engine, seasons=seasons)
    if adv.empty:
        raise ValueError("No advanced stats available for the requested seasons.")

    team_games = _prepare_team_games(games, adv)
    team_features = _compute_team_features(team_games, rolling_window=rolling_window)
    training_df = _merge_game_features(games, team_features)
    if training_df.empty:
        raise ValueError("Failed to construct training features from the available data.")
    return training_df


def _safe_float(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (float, int)):
        if pd.isna(value):
            return None
        return float(value)
    if pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class TeamFeatureSnapshot:
    season_avg_offensive_rating: Optional[float]
    season_avg_defensive_rating: Optional[float]
    season_avg_net_rating: Optional[float]
    season_avg_pace: Optional[float]
    season_avg_true_shooting: Optional[float]
    season_avg_usage: Optional[float]
    recent_avg_point_diff: Optional[float]
    recent_avg_offensive_rating: Optional[float]
    recent_avg_defensive_rating: Optional[float]
    recent_avg_net_rating: Optional[float]
    rest_days: Optional[float]


def _compute_snapshot_for_team(
    base_team_games: pd.DataFrame,
    team_id: int,
    season: int,
    game_date: date,
    rolling_window: int,
) -> TeamFeatureSnapshot:
    team_history = base_team_games[base_team_games["team_id"] == team_id].copy()
    team_history = team_history[team_history["date"] < pd.Timestamp(game_date)]

    placeholder_id = -team_id if team_id > 0 else -999999
    placeholder = {
        "game_id": placeholder_id,
        "team_id": team_id,
        "season": season,
        "date": pd.Timestamp(game_date),
        "team_score": pd.NA,
        "opponent_score": pd.NA,
        "opponent_id": pd.NA,
        "is_home": pd.NA,
        "game_offensive_rating": pd.NA,
        "game_defensive_rating": pd.NA,
        "game_net_rating": pd.NA,
        "game_pace": pd.NA,
        "game_true_shooting_percentage": pd.NA,
        "game_usage_percentage": pd.NA,
    }
    combined = pd.concat([team_history, pd.DataFrame([placeholder])], ignore_index=True, sort=False)
    combined = _compute_team_features(combined, rolling_window=rolling_window)
    snapshot_row = combined[combined["game_id"] == placeholder_id].iloc[0]

    return TeamFeatureSnapshot(
        season_avg_offensive_rating=_safe_float(snapshot_row.get("season_avg_offensive_rating")),
        season_avg_defensive_rating=_safe_float(snapshot_row.get("season_avg_defensive_rating")),
        season_avg_net_rating=_safe_float(snapshot_row.get("season_avg_net_rating")),
        season_avg_pace=_safe_float(snapshot_row.get("season_avg_pace")),
        season_avg_true_shooting=_safe_float(snapshot_row.get("season_avg_true_shooting")),
        season_avg_usage=_safe_float(snapshot_row.get("season_avg_usage")),
        recent_avg_point_diff=_safe_float(snapshot_row.get("recent_avg_point_diff")),
        recent_avg_offensive_rating=_safe_float(snapshot_row.get("recent_avg_offensive_rating")),
        recent_avg_defensive_rating=_safe_float(snapshot_row.get("recent_avg_defensive_rating")),
        recent_avg_net_rating=_safe_float(snapshot_row.get("recent_avg_net_rating")),
        rest_days=_safe_float(snapshot_row.get("rest_days")),
    )


def build_matchup_features(
    engine: Engine,
    home_team_id: int,
    visitor_team_id: int,
    game_date: date,
    season: int,
    rolling_window: int = ROLLING_WINDOW_DEFAULT,
) -> pd.DataFrame:
    games = _load_games(engine, max_season=season)
    games = games[games["date"] < pd.Timestamp(game_date)]

    adv = _load_adv_stats(engine, max_season=season)
    if not games.empty:
        adv = adv[adv["game_id"].isin(games["id"])]

    team_games = _prepare_team_games(games, adv)

    home_snapshot = _compute_snapshot_for_team(
        team_games, home_team_id, season, game_date, rolling_window=rolling_window
    )
    away_snapshot = _compute_snapshot_for_team(
        team_games, visitor_team_id, season, game_date, rolling_window=rolling_window
    )

    data = {
        "season": [season],
        "date": [pd.Timestamp(game_date)],
        "home_team_id": [home_team_id],
        "visitor_team_id": [visitor_team_id],
        "diff_season_net_rating": [
            (home_snapshot.season_avg_net_rating or 0.0)
            - (away_snapshot.season_avg_net_rating or 0.0)
        ],
        "diff_season_off_rating": [
            (home_snapshot.season_avg_offensive_rating or 0.0)
            - (away_snapshot.season_avg_offensive_rating or 0.0)
        ],
        "diff_season_def_rating": [
            (home_snapshot.season_avg_defensive_rating or 0.0)
            - (away_snapshot.season_avg_defensive_rating or 0.0)
        ],
        "diff_season_pace": [
            (home_snapshot.season_avg_pace or 0.0) - (away_snapshot.season_avg_pace or 0.0)
        ],
        "diff_recent_point_diff": [
            (home_snapshot.recent_avg_point_diff or 0.0)
            - (away_snapshot.recent_avg_point_diff or 0.0)
        ],
        "diff_recent_net_rating": [
            (home_snapshot.recent_avg_net_rating or 0.0)
            - (away_snapshot.recent_avg_net_rating or 0.0)
        ],
        "home_rest_days": [home_snapshot.rest_days if home_snapshot.rest_days is not None else 3.0],
        "away_rest_days": [away_snapshot.rest_days if away_snapshot.rest_days is not None else 3.0],
    }
    df = pd.DataFrame(data)

    diff_cols = [
        "diff_season_net_rating",
        "diff_season_off_rating",
        "diff_season_def_rating",
        "diff_season_pace",
        "diff_recent_point_diff",
        "diff_recent_net_rating",
    ]
    df[diff_cols] = df[diff_cols].fillna(0.0)

    rest_cols = ["home_rest_days", "away_rest_days"]
    df[rest_cols] = df[rest_cols].fillna(3.0)
    return df


__all__ = [
    "ROLLING_WINDOW_DEFAULT",
    "MODEL_FEATURE_COLUMNS",
    "get_training_dataframe",
    "build_matchup_features",
]

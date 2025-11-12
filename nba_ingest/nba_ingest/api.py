"""FastAPI application exposing NBA win probabilities and betting odds."""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, joinedload

from .balldontlie_client import BallDontLieClient
from .config import Settings, load_settings
from .db import create_db_engine, create_session_factory
from .features import MODEL_FEATURE_COLUMNS
from .models import Base, NBAGame, NBAGameOdds
from .odds_ingest import ingest_odds_payloads
from .odds_math import (
    american_to_decimal,
    american_to_implied_prob,
    decimal_to_american,
    parlay_ev,
    parlay_probability,
    remove_vig_two_way,
)
from .win_prob_model import build_single_matchup_features, load_model

logger = logging.getLogger(__name__)

app = FastAPI(title="NBA Win Probability API", version="0.3.0")

_settings: Optional[Settings] = None
_engine: Optional[Engine] = None
_session_factory = None
_client: Optional[BallDontLieClient] = None
_model = None
_model_path: Path = Path(os.environ.get("WIN_PROB_MODEL_PATH", "models/win_prob_model.pkl"))


class TeamInfo(BaseModel):
    id: int
    abbreviation: str
    full_name: str


class MarketOdds(BaseModel):
    vendor: str
    line_type: str
    home_price: Optional[int] = None
    away_price: Optional[int] = None
    spread_points: Optional[float] = None
    total_points: Optional[float] = None
    over_price: Optional[int] = None
    under_price: Optional[int] = None
    implied_home_win_prob: Optional[float] = None
    implied_away_win_prob: Optional[float] = None
    fair_home_win_prob: Optional[float] = None
    fair_away_win_prob: Optional[float] = None


class GameWithOddsAndModel(BaseModel):
    game_id: int
    date: date
    season: int
    home_team: TeamInfo
    away_team: TeamInfo
    home_score: Optional[int]
    away_score: Optional[int]
    model_home_win_prob: Optional[float]
    markets: List[MarketOdds]
    best_moneyline_home: Optional[MarketOdds]
    best_moneyline_away: Optional[MarketOdds]


class ParlayLegRequest(BaseModel):
    game_id: int
    line_type: str
    side: str
    american_odds: int


class ParlayEstimateRequest(BaseModel):
    legs: List[ParlayLegRequest]
    stake: float = 1.0
    parlay_offered_american_odds: Optional[int] = None


class ParlayEstimateResponse(BaseModel):
    p_parlay: float
    fair_decimal_odds: Optional[float]
    fair_american_odds: Optional[int]
    offered_decimal_odds: Optional[float] = None
    offered_american_odds: Optional[int] = None
    ev: Optional[float] = None


def _get_session() -> Session:
    if _session_factory is None:
        raise RuntimeError("Database session factory is not initialized.")
    session: Session = _session_factory()
    return session


def get_db_session() -> Iterable[Session]:
    session = _get_session()
    try:
        yield session
    finally:
        session.close()


def _load_model_once() -> None:
    global _model
    if _model is not None:
        return
    try:
        _model = load_model(_model_path)
        logger.info("Loaded win probability model from %s", _model_path)
    except FileNotFoundError:
        logger.warning("Win probability model file not found at %s", _model_path)
        _model = None


@app.on_event("startup")
def startup_event() -> None:
    global _settings, _engine, _session_factory, _client
    _settings = load_settings()
    _engine = create_db_engine(_settings.database_url)
    Base.metadata.create_all(_engine)
    _session_factory = create_session_factory(_engine)
    _client = BallDontLieClient(_settings.api_key)
    _load_model_once()


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


def _parse_date(date_str: Optional[str]) -> date:
    if not date_str:
        return datetime.now(timezone.utc).date()
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.") from exc


def _fetch_cached_odds(session: Session, game_ids: List[int], freshness_minutes: int = 30) -> Dict[int, List[NBAGameOdds]]:
    if not game_ids:
        return {}
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=freshness_minutes)
    odds = (
        session.execute(
            select(NBAGameOdds).where(NBAGameOdds.game_id.in_(game_ids)).where(NBAGameOdds.last_update >= cutoff)
        )
        .scalars()
        .all()
    )
    grouped: Dict[int, List[NBAGameOdds]] = {}
    for record in odds:
        grouped.setdefault(record.game_id, []).append(record)
    return grouped


def _ensure_odds_for_games(session: Session, game_ids: List[int]) -> Dict[int, List[NBAGameOdds]]:
    cached = _fetch_cached_odds(session, game_ids)
    missing = [gid for gid in game_ids if not cached.get(gid)]
    if missing and _client is not None:
        payloads = _client.list_odds_by_game_ids(missing)
        if payloads:
            ingest_odds_payloads(session, payloads)
            session.commit()
            cached = _fetch_cached_odds(session, game_ids)
    return cached


def _build_market_model(record: NBAGameOdds) -> MarketOdds:
    implied_home = implied_away = fair_home = fair_away = None
    if record.line_type == "moneyline":
        implied_home = american_to_implied_prob(record.home_team_price)
        implied_away = american_to_implied_prob(record.away_team_price)
        if implied_home is not None and implied_away is not None:
            fair_home, fair_away = remove_vig_two_way(implied_home, implied_away)
    return MarketOdds(
        vendor=record.vendor,
        line_type=record.line_type,
        home_price=record.home_team_price,
        away_price=record.away_team_price,
        spread_points=record.spread_points,
        total_points=record.total_points,
        over_price=record.over_price,
        under_price=record.under_price,
        implied_home_win_prob=implied_home,
        implied_away_win_prob=implied_away,
        fair_home_win_prob=fair_home,
        fair_away_win_prob=fair_away,
    )


def _select_best_market(markets: List[MarketOdds], side: str) -> Optional[MarketOdds]:
    best: Optional[MarketOdds] = None
    best_price: Optional[int] = None
    for market in markets:
        price = market.home_price if side == "home" else market.away_price
        if price is None:
            continue
        if best_price is None or price > best_price:
            best = market
            best_price = price
    return best


def _predict_home_probability(game: NBAGame) -> Optional[float]:
    if _model is None or _engine is None:
        return None
    try:
        features = build_single_matchup_features(
            _engine,
            home_team_id=game.home_team_id,
            visitor_team_id=game.visitor_team_id,
            game_date=game.date,
        )
        probabilities = _model.predict_proba(features[MODEL_FEATURE_COLUMNS])[:, 1]
        return float(probabilities[0])
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.debug("Model prediction failed for game %s: %s", game.id, exc)
        return None


@app.get("/games", response_model=List[GameWithOddsAndModel])
def get_games(
    date_param: Optional[str] = Query(None, description="Target date in YYYY-MM-DD format."),
    session: Session = Depends(get_db_session),
) -> List[GameWithOddsAndModel]:
    target_date = _parse_date(date_param)
    games = (
        session.execute(
            select(NBAGame)
            .where(NBAGame.date == target_date)
            .options(joinedload(NBAGame.home_team), joinedload(NBAGame.visitor_team))
        )
        .unique()
        .scalars()
        .all()
    )
    if not games:
        return []

    game_ids = [game.id for game in games]
    odds_by_game = _ensure_odds_for_games(session, game_ids)

    response: List[GameWithOddsAndModel] = []
    for game in games:
        home_team = game.home_team
        away_team = game.visitor_team
        markets = [_build_market_model(record) for record in odds_by_game.get(game.id, [])]
        moneyline_markets = [market for market in markets if market.line_type == "moneyline"]
        response.append(
            GameWithOddsAndModel(
                game_id=game.id,
                date=game.date,
                season=game.season,
                home_team=TeamInfo(
                    id=home_team.id if home_team else game.home_team_id,
                    abbreviation=home_team.abbreviation if home_team else "HOME",
                    full_name=home_team.full_name if home_team else "Home Team",
                ),
                away_team=TeamInfo(
                    id=away_team.id if away_team else game.visitor_team_id,
                    abbreviation=away_team.abbreviation if away_team else "AWAY",
                    full_name=away_team.full_name if away_team else "Away Team",
                ),
                home_score=game.home_team_score,
                away_score=game.visitor_team_score,
                model_home_win_prob=_predict_home_probability(game),
                markets=markets,
                best_moneyline_home=_select_best_market(moneyline_markets, "home"),
                best_moneyline_away=_select_best_market(moneyline_markets, "away"),
            )
        )
    return response


@app.post("/parlay/estimate", response_model=ParlayEstimateResponse)
def estimate_parlay(request: ParlayEstimateRequest) -> ParlayEstimateResponse:
    if not request.legs:
        raise HTTPException(status_code=400, detail="At least one leg is required.")
    leg_probs: List[float] = []
    for leg in request.legs:
        implied = american_to_implied_prob(leg.american_odds)
        if implied is None:
            raise HTTPException(status_code=400, detail="Invalid American odds provided for a leg.")
        leg_probs.append(implied)
    p_parlay = parlay_probability(leg_probs)
    fair_decimal = 1.0 / p_parlay if p_parlay > 0 else None
    fair_american = decimal_to_american(fair_decimal) if fair_decimal else None
    offered_decimal = None
    ev_value = None
    if request.parlay_offered_american_odds is not None:
        offered_decimal = american_to_decimal(request.parlay_offered_american_odds)
        ev_info = parlay_ev(leg_probs, offered_decimal, stake=request.stake)
        ev_value = ev_info["ev"]
        # parlay_ev already computes the probability; ensure consistency.
        p_parlay = ev_info["p_parlay"]
        if fair_decimal is None:
            fair_decimal = ev_info.get("fair_decimal_odds")
            if fair_decimal:
                fair_american = decimal_to_american(fair_decimal)
    return ParlayEstimateResponse(
        p_parlay=p_parlay,
        fair_decimal_odds=fair_decimal,
        fair_american_odds=fair_american,
        offered_decimal_odds=offered_decimal,
        offered_american_odds=request.parlay_offered_american_odds,
        ev=ev_value,
    )


__all__ = ["app"]

"""Helper functions for working with betting odds and probabilities."""
from __future__ import annotations

from functools import reduce
from typing import Iterable, List, Optional


def american_to_implied_prob(odds: Optional[int]) -> Optional[float]:
    """Convert American odds to implied probability."""
    if odds is None:
        return None
    if odds == 0:
        return None
    if odds > 0:
        return 100 / (odds + 100)
    return -odds / (-odds + 100)


def remove_vig_two_way(p_home: Optional[float], p_away: Optional[float]) -> tuple[Optional[float], Optional[float]]:
    """Remove vigorish from a two-way market, returning fair probabilities."""
    if p_home is None or p_away is None:
        return None, None
    total = p_home + p_away
    if total <= 0:
        return None, None
    return p_home / total, p_away / total


def american_to_decimal(odds: int) -> float:
    """Convert American odds to decimal odds."""
    if odds >= 0:
        return 1 + odds / 100
    return 1 + 100 / (-odds)


def decimal_to_american(decimal_odds: float) -> int:
    """Convert decimal odds to American format."""
    if decimal_odds <= 1:
        raise ValueError("Decimal odds must be greater than 1.0")
    if decimal_odds >= 2.0:
        return int(round((decimal_odds - 1) * 100))
    return int(round(-100 / (decimal_odds - 1)))


def parlay_probability(leg_probs: Iterable[float]) -> float:
    """Compute the combined probability of all legs hitting."""
    probs: List[float] = [p for p in leg_probs if p is not None]
    if not probs:
        return 0.0
    return reduce(lambda acc, value: acc * value, probs, 1.0)


def parlay_ev(leg_probs: Iterable[float], parlay_decimal_odds: float, stake: float = 1.0) -> dict:
    """Calculate expected value information for a parlay bet."""
    probs_list = [p for p in leg_probs if p is not None]
    p_parlay = parlay_probability(probs_list)
    payout_if_hit = stake * (parlay_decimal_odds - 1)
    ev = p_parlay * payout_if_hit - (1 - p_parlay) * stake
    fair_decimal_odds = 1.0 / p_parlay if p_parlay > 0 else None
    result = {
        "p_parlay": p_parlay,
        "fair_decimal_odds": fair_decimal_odds,
        "ev": ev,
    }
    return result


__all__ = [
    "american_to_implied_prob",
    "remove_vig_two_way",
    "decimal_to_american",
    "american_to_decimal",
    "parlay_probability",
    "parlay_ev",
]

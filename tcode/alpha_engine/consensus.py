"""
TSLA Alpha Engine: Multi-Model Consensus Layer
Enforces corroboration between independent probabilistic models to mitigate hallucinations.
"""
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from enum import Enum, auto
from typing import List, Dict, Optional
import numpy as np


def compute_expiry(dte_str: str) -> str:
    """Convert '7DTE', '14DTE', '0DTE' to next valid Friday >= N days from today.

    DEPRECATED: Use find_best_expiry() instead.  This function guesses a Friday
    which may not match any actual Tradier expiration (TSLA has daily expirations).
    Retained as a last-resort fallback only.
    """
    try:
        days = int(dte_str.replace('DTE', '').strip())
    except ValueError:
        days = 7
    target = datetime.now() + timedelta(days=days)
    # Advance to Friday (weekday 4) if not already
    days_until_friday = (4 - target.weekday()) % 7
    expiry = target + timedelta(days=days_until_friday)
    return expiry.strftime('%Y-%m-%d')


def find_best_expiry(dte_target: int) -> str:
    """Return the actual Tradier expiry date closest to dte_target days from today.

    Fetches the real expiry list from the options chain cache (Tradier-sourced).
    Picks the candidate whose DTE is closest to dte_target, preferring candidates
    at or above the target over those below (i.e., sort key: (abs(dte - target), -(dte))).

    Falls back to compute_expiry() if the chain cache is unavailable.
    """
    try:
        from ingestion.options_chain import get_chain_cache
        expiries = get_chain_cache()._get_expiry_list()
    except Exception:
        expiries = []

    today = date.today()
    candidates = []
    for exp_str in expiries:
        try:
            exp_date = date.fromisoformat(exp_str)
        except ValueError:
            continue
        dte = (exp_date - today).days
        if dte >= max(dte_target - 2, 0):  # allow 2-day fudge below target
            candidates.append((abs(dte - dte_target), -dte, exp_str))

    if candidates:
        candidates.sort()
        chosen = candidates[0][2]
        return chosen

    # Absolute fallback: nearest future expiry in the list
    future = [e for e in expiries if date.fromisoformat(e) >= today]
    if future:
        import logging
        logging.getLogger("consensus").warning(
            "find_best_expiry(%d): no candidate within ±2 days of target; "
            "falling back to nearest future expiry %s", dte_target, future[0]
        )
        return future[0]

    # Last resort: calendar-based Friday estimate (no Tradier data)
    import logging
    logging.getLogger("consensus").warning(
        "find_best_expiry(%d): no expiry list available; using compute_expiry fallback",
        dte_target,
    )
    return compute_expiry(f"{dte_target}DTE")


def find_best_expiry_for_archetype(archetype_name: str) -> str:
    """Return the actual Tradier expiry best matching the archetype's prefer_ttm_days range.

    Reads the GreeksProfile for the archetype to get (min_dte, max_dte) and targets
    the midpoint.  Falls back to 7DTE if the archetype has no Greeks profile.
    """
    try:
        from config.archetypes import get_greeks_profile
        profile = get_greeks_profile(archetype_name)
        if profile and profile.prefer_ttm_days:
            min_dte, max_dte = profile.prefer_ttm_days
            target = (min_dte + max_dte) // 2
        else:
            target = 7
    except Exception:
        target = 7
    return find_best_expiry(target)

class ModelType(Enum):
    SENTIMENT = auto()    # NLP-based sentiment from news/social
    OPTIONS_FLOW = auto() # Real-time block sweep analysis
    MACRO = auto()        # Correlation with NDX/SPY and Interest Rates
    VOLATILITY = auto()   # VIX and IV Surface analysis
    CONTRARIAN = auto()  # Mean reversion against overextended moves
    EV_SECTOR = auto()   # Sector-wide EV moves affecting TSLA
    PREMARKET = auto()   # Pre-market futures and overnight signals

class SignalDirection(Enum):
    BULLISH = 1
    BEARISH = -1
    NEUTRAL = 0

@dataclass(slots=True)
class ModelSignal:
    """
    Individual signal from a specific ML model.
    Includes conviction, context, and strategy metadata.
    """
    model_id: ModelType
    direction: SignalDirection
    confidence: float
    timestamp: float
    ticker: str = "TSLA"
    underlying_price: float = 0.0
    price_source: str = "IBKR-L1"
    strategy_code: str = "STRAT-000"
    recommended_strike: float = 0.0 # Legacy support for single leg
    short_strike: float = 0.0
    long_strike: float = 0.0
    recommended_expiry: str = "7DTE"
    option_type: str = "CALL" # CALL or PUT
    action: str = "BUY" # BUY or SELL
    is_spread: bool = False
    expiration_date: str = field(default_factory=lambda: compute_expiry("7DTE"))
    target_limit_price: float = 0.0
    take_profit_price: float = 0.0
    stop_loss_price: float = 0.0
    stop_loss_underlying_price: float = 0.0  # Phase 9: underlying stop for option SL leg (0 = derive from spot)
    kelly_wager_pct: float = 0.0
    quantity: int = 0
    confidence_rationale: str = "No rationale provided."
    implied_volatility: float = 0.0

class ConsensusRejection(Exception):
    """Raised when models are in heavy disagreement."""
    pass

class ConsensusEngine:
    """
    The 'Signal Guard' that prevents single-model hallucinations from triggering trades.
    Requires an ensemble of models to reach a minimum agreement threshold.
    """
    
    def __init__(self, agreement_threshold: float = 0.6, min_models: int = 3):
        self.agreement_threshold = agreement_threshold
        self.min_models = min_models

    def aggregate_signals(self, signals: List[ModelSignal]) -> Optional[ModelSignal]:
        """
        Calculates a weighted consensus signal.
        
        Logic:
            1. Ensure minimum number of models have reported.
            2. Sum the direction weighted by confidence.
            3. If the net agreement magnitude exceeds threshold, return the aggregate.
            
        Academic Rigor: Using a weighted average of probabilistic outcomes (Bayesian-lite)
        to determine the ensemble's collective conviction.
        """
        if len(signals) < self.min_models:
            return None

        # Extract directions as scalars: Bullish (1), Bearish (-1), Neutral (0)
        # Weights are the confidence scores.
        directions = np.array([s.direction.value for s in signals])
        confidences = np.array([s.confidence for s in signals])
        
        # Weighted sum of directions
        weighted_sum = np.dot(directions, confidences)
        max_possible_weight = np.sum(confidences)
        
        if max_possible_weight == 0:
            return None
            
        # Agreement score (-1.0 to 1.0)
        agreement_score = weighted_sum / max_possible_weight
        
        # Check if consensus exceeds threshold
        if abs(agreement_score) >= self.agreement_threshold:
            consensus_direction = SignalDirection.BULLISH if agreement_score > 0 else SignalDirection.BEARISH
            
            # Aggregate confidence is the mean of the reporting models
            avg_confidence = np.mean(confidences)
            
            return ModelSignal(
                model_id=ModelType.MACRO, # Model_id is symbolic for consensus output
                direction=consensus_direction,
                confidence=avg_confidence * abs(agreement_score), # Penalize confidence by disagreement
                timestamp=max(s.timestamp for s in signals)
            )
            
        return None

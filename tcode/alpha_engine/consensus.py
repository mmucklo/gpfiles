"""
TSLA Alpha Engine: Multi-Model Consensus Layer
Enforces corroboration between independent probabilistic models to mitigate hallucinations.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import List, Dict, Optional
import numpy as np


def compute_expiry(dte_str: str) -> str:
    """Convert '7DTE', '14DTE', '0DTE' to next valid Friday >= N days from today."""
    try:
        days = int(dte_str.replace('DTE', '').strip())
    except ValueError:
        days = 7
    target = datetime.now() + timedelta(days=days)
    # Advance to Friday (weekday 4) if not already
    days_until_friday = (4 - target.weekday()) % 7
    expiry = target + timedelta(days=days_until_friday)
    return expiry.strftime('%Y-%m-%d')

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

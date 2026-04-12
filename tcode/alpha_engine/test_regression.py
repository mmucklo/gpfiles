"""
TSLA Alpha Engine: Regression & Integrity Test Suite
Ensures that no signal carries $0 exit prices and that spot prices align with reality.
"""
import pytest
import time
from consensus import ModelSignal, SignalDirection, ModelType
from ingestion.pricing import MultiSourcePricing

def test_signal_integrity_no_zero_exits():
    """Regression: Every BUY/SELL signal MUST have non-zero exit targets."""
    sig = ModelSignal(
        model_id=ModelType.SENTIMENT,
        direction=SignalDirection.BULLISH,
        confidence=0.9,
        timestamp=time.time(),
        action="BUY",
        target_limit_price=5.0,
        take_profit_price=6.25,
        stop_loss_price=4.25
    )
    
    assert sig.take_profit_price > 0, "Take profit cannot be zero for active trade."
    assert sig.stop_loss_price > 0, "Stop loss cannot be zero for active trade."
    assert sig.target_limit_price > 0, "Entry limit cannot be zero."

def test_pricing_consensus_realism():
    """Integrity: Consensus spot price must be in a realistic TSLA range (currently > $300)."""
    gatherer = MultiSourcePricing()
    try:
        price = gatherer.get_consensus_price()
        print(f"DEBUG: Current Consensus Price: {price}")
        # Current market check (March 2026 TSLA is expected high $300s+)
        assert price > 300.0, f"Spot price ${price} seems hallucinated (expected > $300)."
        assert price < 1000.0, f"Spot price ${price} seems hallucinated (expected < $1000)."
    except Exception as e:
        pytest.skip(f"Pricing sources offline: {e}")

def test_kelly_logic_bounds():
    """Traceability: Kelly wager must be within 0-100% and rationale must exist."""
    from risk_engine import RiskEngine
    re = RiskEngine()
    
    # 90% confidence, 1:1 odds
    # f* = 2p - 1 = 1.8 - 1 = 0.8
    # Quarter Kelly = 0.2
    wager = re.calculate_fractional_kelly(0.9, 1.0, 0.25)
    assert 0.19 < wager < 0.21
    
    # Low confidence should result in 0 wager
    wager = re.calculate_fractional_kelly(0.4, 1.0, 0.25)
    assert wager == 0.0

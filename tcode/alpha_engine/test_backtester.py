import pytest
from backtester import Backtester, MarketEvent
from consensus import ModelSignal, SignalDirection, ModelType

def test_backtester_loop_pnl_generation():
    """Test that the backtester correctly generates PnL for successful consensus."""
    bt = Backtester(initial_capital=100_000.0)
    
    # 3 bullish signals with 1.0 confidence = 100% agreement score
    # Fractional Kelly (f=0.25) for 100% win prob (1:1 risk/reward) = 0.25 * 1.0 = 0.25 wager (capped by Kelly)
    # Actually Kelly calculation: f* = (1.0 * 1.0 - 0.0) / 1.0 = 1.0
    # Quarter Kelly = 0.25
    signals = [
        ModelSignal(ModelType.SENTIMENT, SignalDirection.BULLISH, 1.0, 100),
        ModelSignal(ModelType.OPTIONS_FLOW, SignalDirection.BULLISH, 1.0, 101),
        ModelSignal(ModelType.MACRO, SignalDirection.BULLISH, 1.0, 102),
    ]
    
    event = MarketEvent(
        timestamp=1000.0,
        price=200.0,
        iv=0.5,
        vix=20.0,
        spy_trend_bearish=False,
        days_to_earnings=30,
        delta=0.5,
        signals=signals
    )
    
    equity = bt.run([event])
    
    # PnL logic: capital * approved_wager * 0.05
    # 100,000 * 0.25 * 0.05 = 1,250 profit
    assert bt.portfolio_value == 101250.0
    assert len(equity) == 1

def test_backtester_risk_rejection():
    """Test that the backtester correctly skips trades rejected by the risk engine."""
    bt = Backtester(initial_capital=100_000.0)
    
    # Bullish signals but hit an Earnings anti-pattern (AP-001: < 7 days from earnings)
    signals = [
        ModelSignal(ModelType.SENTIMENT, SignalDirection.BULLISH, 1.0, 100),
        ModelSignal(ModelType.OPTIONS_FLOW, SignalDirection.BULLISH, 1.0, 101),
        ModelSignal(ModelType.MACRO, SignalDirection.BULLISH, 1.0, 102),
    ]
    
    event = MarketEvent(
        timestamp=1000.0,
        price=200.0,
        iv=0.5,
        vix=20.0,
        spy_trend_bearish=False,
        days_to_earnings=3, # Violates AP-001
        delta=0.5,
        signals=signals
    )
    
    equity = bt.run([event])
    
    # Portfolio value should remain at initial capital
    assert bt.portfolio_value == 100000.0
    assert equity[0] == 100000.0

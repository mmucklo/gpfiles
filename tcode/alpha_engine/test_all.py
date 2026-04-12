import pytest
import asyncio
from ingestion.pricing import MultiSourcePricing
from simulation import SimulationEngine, PDTGuard, FillModel
from risk_engine import RiskEngine, TradeProposal, PositionType, SentimentTrigger
from consensus import SignalDirection, ModelSignal, ModelType

@pytest.mark.asyncio
async def test_triple_source_pricing():
    pricing = MultiSourcePricing()
    # Mocking might be better but user wants REAL verification
    # We at least check that we get a float in a reasonable range
    price = pricing.get_consensus_price()
    assert isinstance(price, float)
    assert price > 100.0 # TSLA hasn't been <100 in a while

def test_pdt_guard():
    # Scenario 1: Equity < 25k, 3 trades in 5 days
    pdt = PDTGuard(20000.0)
    assert pdt.can_trade() is True
    pdt.record_day_trade()
    pdt.record_day_trade()
    pdt.record_day_trade()
    assert pdt.can_trade() is False
    
    # Scenario 2: Equity >= 25k
    pdt_whale = PDTGuard(30000.0)
    pdt_whale.record_day_trade()
    pdt_whale.record_day_trade()
    pdt_whale.record_day_trade()
    pdt_whale.record_day_trade()
    assert pdt_whale.can_trade() is True

def test_fill_model():
    price = 100.0
    buy_fill = FillModel.get_fill_price(price, "BUY")
    sell_fill = FillModel.get_fill_price(price, "SELL")
    assert buy_fill > price
    assert sell_fill < price
    # Check slippage range
    assert buy_fill < price * 1.05
    assert sell_fill > price * 0.95

@pytest.mark.asyncio
async def test_simulation_engine_pnl():
    sim = SimulationEngine(initial_capital=25000.0)
    # Mock a winning signal
    sig = ModelSignal(
        model_id=ModelType.SENTIMENT,
        direction=SignalDirection.BULLISH,
        confidence=0.9,
        timestamp=0,
        ticker="TSLA",
        underlying_price=200.0,
        price_source="MOCK",
        strategy_code="UNIT_TEST",
        recommended_strike=210.0,
        recommended_expiry="7DTE",
        option_type="CALL",
        action="BUY",
        expiration_date="2026-01-01",
        target_limit_price=5.0
    )
    
    # 1. Open position
    await sim.handle_signal(sig)
    assert len(sim.positions) == 1
    
    # 2. Simulate price move up (Bullish win)
    # We need to mock get_consensus_price to return a higher value
    sim.pricing.get_consensus_price = lambda: 210.0 # 5% move up
    
    await sim.update_market_state()
    # Delta=0.5, Leveraged 10x in sim.py update_market_state
    # 5% spot move = 50% option move
    
    await sim.close_positions()
    assert sim.equity > 25000.0
    assert sim.stats["wins"] == 1

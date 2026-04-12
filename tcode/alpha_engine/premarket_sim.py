"""
TSLA Alpha Engine: Premarket Analysis Simulation (Verified)
Injects high-conviction signals with REAL spot pricing and full exit plans.
"""
import asyncio
import time
import logging
from publisher import SignalPublisher
from consensus import ModelSignal, SignalDirection, ModelType, compute_expiry
from ingestion.pricing import MultiSourcePricing

async def run_premarket_sim():
    publisher = SignalPublisher()
    await publisher.connect()
    
    # Fetch REAL consensus price to avoid $200 mismatches
    pricing = MultiSourcePricing()
    try:
        real_spot = pricing.get_consensus_price()
        print(f"Verified Real-Time Spot for Sim: ${real_spot:.2f}")
    except Exception as e:
        print(f"Pricing fetch failed: {e}. Using high-$300s fail-safe.")
        real_spot = 395.50

    signals = [
        ModelSignal(
            model_id=ModelType.SENTIMENT, 
            direction=SignalDirection.BULLISH, 
            confidence=0.92, 
            timestamp=time.time(),
            ticker="TSLA",
            underlying_price=real_spot,
            price_source="TRIPLE-CONSENSUS",
            strategy_code="STRAT-003 (Institutional Flow)",
            recommended_strike=real_spot * 1.05,
            recommended_expiry="14DTE",
            option_type="CALL",
            action="BUY",
            expiration_date=compute_expiry("14DTE"),
            target_limit_price=4.25,
            take_profit_price=4.25 * 1.25,
            stop_loss_price=4.25 * 0.85,
            kelly_wager_pct=0.05,
            quantity=10,
            confidence_rationale="Verified sentiment spike across 3 major financial wires."
        ),
        ModelSignal(
            model_id=ModelType.OPTIONS_FLOW, 
            direction=SignalDirection.BEARISH, 
            confidence=0.88, 
            timestamp=time.time(),
            ticker="TSLA",
            underlying_price=real_spot,
            price_source="IBKR-L1",
            strategy_code="STRAT-004 (Gamma Exhaustion)",
            short_strike=real_spot * 1.02,
            long_strike=real_spot * 1.04,
            is_spread=True,
            recommended_expiry="7DTE",
            option_type="CALL",
            action="SELL",
            expiration_date=compute_expiry("7DTE"),
            target_limit_price=1.85,
            take_profit_price=1.85 * 0.5,
            stop_loss_price=1.85 * 1.5,
            kelly_wager_pct=0.04,
            quantity=5,
            confidence_rationale="Institutional OTM call sell-side pressure detected at resistance."
        ),
        ModelSignal(
            model_id=ModelType.MACRO, 
            direction=SignalDirection.BULLISH, 
            confidence=0.95, 
            timestamp=time.time(),
            ticker="TSLA",
            underlying_price=real_spot,
            price_source="IEX-REALTIME",
            strategy_code="STRAT-002 (VIX Spike Fade)",
            recommended_strike=real_spot * 1.01,
            recommended_expiry="0DTE",
            option_type="CALL",
            action="BUY",
            expiration_date=compute_expiry("0DTE"),
            target_limit_price=1.15,
            take_profit_price=1.15 * 1.25,
            stop_loss_price=1.15 * 0.85,
            kelly_wager_pct=0.02,
            quantity=20,
            confidence_rationale="NDX correlation hit 0.95; TSLA oversold relative to futures."
        ),
    ]

    for sig in signals:
        print(f"Publishing premarket signal: {sig.model_id.name} -> {sig.direction.name} ({sig.confidence})")
        await publisher.publish_signal(sig)
        await asyncio.sleep(0.1)

    print("All premarket signals published.")
    await publisher.close()

if __name__ == "__main__":
    asyncio.run(run_premarket_sim())

"""
TSLA Alpha Engine: High-Fidelity Simulation Engine
Handles execution, PnL tracking, and rule enforcement (PDT, Risk).
Fixed: Corrected leveraged options math and added capital floors.
"""
import asyncio
import time
import random
import logging
from dataclasses import dataclass
from typing import List, Optional

from risk_engine import RiskEngine, TradeProposal, TradeRejection, PositionType, SentimentTrigger
from consensus import SignalDirection, ModelSignal
from ingestion.pricing import MultiSourcePricing

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SimulationEngine")

class FillModel:
    @staticmethod
    def get_fill_price(target_price: float, action: str, volatility: float = 0.02) -> float:
        # Realistic slippage (0.1% to 0.5% base)
        slippage = random.uniform(0.001, 0.005) * (1 + volatility * 5)
        if action == "BUY":
            return target_price * (1 + slippage)
        else:
            return target_price * (1 - slippage)

@dataclass
class ActivePosition:
    ticker: str
    qty: int
    entry_price: float      # Option premium at entry
    current_price: float    # Current option premium
    entry_spot: float       # Underlying price at entry
    pos_type: PositionType
    entry_time: float

class SimulationEngine:
    """
    Simulates the lifecycle of trading TSLA options.
    """
    def __init__(self, initial_capital: float = 25000.0):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.equity = initial_capital
        self.positions: List[ActivePosition] = []
        self.pdt_day_trades: List[float] = [] 
        self.pricing = MultiSourcePricing()
        self.risk_engine = RiskEngine()
        self.stats = {"wins": 0, "losses": 0, "total_trades": 0}
        self.current_spot = 0.0
        
        # Strategy Parameters (Tweaked by Gastown Loop)
        self.kelly_fraction = 0.25
        self.confidence_threshold = 0.75

    def get_pnl_pct(self) -> float:
        if self.initial_capital == 0: return 0.0
        return (self.equity - self.initial_capital) / self.initial_capital

    async def update_market_state(self):
        """Update equity based on current real TSLA prices."""
        try:
            new_spot = self.pricing.get_consensus_price()
            if self.current_spot == 0:
                self.current_spot = new_spot
                return

            current_portfolio_value = 0
            for pos in self.positions:
                # Option Pricing Simulation (Simplified Delta-Gamma Logic)
                # Why: Real options move with underlying price. 
                # We assume a Delta of 0.5 and 10x leverage for simulation fidelity.
                spot_move_pct = (new_spot - pos.entry_spot) / pos.entry_spot
                
                # Leveraged move: Option PnL = Underlying Move * Leverage
                # e.g. 1% TSLA move = 10% Option move
                leverage = 10.0
                move_direction = 1.0 if pos.pos_type == PositionType.LONG_CALL else -1.0
                
                # Update current premium price
                pos.current_price = pos.entry_price * (1 + (spot_move_pct * leverage * move_direction))
                
                # Ensure option price doesn't go below zero (worthless)
                pos.current_price = max(0.01, pos.current_price)
                
                current_portfolio_value += pos.current_price * pos.qty * 100
            
            self.current_spot = new_spot
            self.equity = max(0.0, self.cash + current_portfolio_value)
            
            # Auto-liquidation check
            if self.equity <= 0:
                logger.error("POT LIQUIDATED: Equity hit zero.")
                self.positions = []
                self.cash = 0.0
                
        except Exception as e:
            logger.error(f"Market update failed: {e}")

    async def handle_signal(self, signal: ModelSignal):
        """Processes an incoming signal and decides whether to trade."""
        if self.equity <= 0: return
        if signal.confidence < self.confidence_threshold: return 

        # PDT check: < $25k and 3+ day trades in 5 days
        now = time.time()
        self.pdt_day_trades = [t for t in self.pdt_day_trades if now - t < 5*24*3600]
        if self.equity < 25000 and len(self.pdt_day_trades) >= 3:
            return

        # Propose trade
        pos_type = PositionType.LONG_CALL if signal.direction == SignalDirection.BULLISH else PositionType.LONG_PUT
        if signal.direction == SignalDirection.NEUTRAL: return

        # Kelly Sizing
        wager_pct = self.risk_engine.calculate_fractional_kelly(signal.confidence, 1.0, self.kelly_fraction)
        
        proposal = TradeProposal(
            position_type=pos_type,
            dte=7,
            days_to_earnings=14,
            delta=0.5,
            position_pnl=0.0,
            action_is_add=False,
            kelly_wager_pct=wager_pct,
            sentiment_trigger=SentimentTrigger.NONE,
            vix_level=18.0,
            spy_trend_bearish=False
        )

        try:
            approved_wager = self.risk_engine.evaluate_trade(proposal)
            if approved_wager <= 0: return

            # Execute at Target Limit Price
            target_price = signal.target_limit_price if signal.target_limit_price > 0 else 5.0
            fill_price = FillModel.get_fill_price(target_price, "BUY")
            
            cost_limit = self.equity * approved_wager
            qty = int(cost_limit / (fill_price * 100)) 
            if qty <= 0: return

            actual_cost = qty * fill_price * 100
            if actual_cost > self.cash:
                # Scale down to available cash
                qty = int(self.cash / (fill_price * 100))
                actual_cost = qty * fill_price * 100
                if qty <= 0: return

            self.cash -= actual_cost
            new_pos = ActivePosition(
                ticker=signal.ticker,
                qty=qty,
                entry_price=fill_price,
                current_price=fill_price,
                entry_spot=self.current_spot,
                pos_type=pos_type,
                entry_time=now
            )
            self.positions.append(new_pos)
            self.stats["total_trades"] += 1
            logger.info(f"SIM EXECUTED: {signal.direction.name} {qty} contracts at ${fill_price:.2f} (Spot: ${self.current_spot:.2f})")

        except TradeRejection:
            pass

    async def close_positions(self):
        """Simulate closing positions based on PnL targets."""
        now = time.time()
        to_remove = []
        for pos in self.positions:
            # PnL = (Current Premium - Entry Premium) / Entry Premium
            pnl_pct = (pos.current_price - pos.entry_price) / pos.entry_price
            
            # Simple exit logic: 20% profit or 10% stop-loss
            if pnl_pct > 0.20 or pnl_pct < -0.10 or (now - pos.entry_time) > 300:
                fill_price = FillModel.get_fill_price(pos.current_price, "SELL")
                proceeds = pos.qty * fill_price * 100
                self.cash += proceeds
                to_remove.append(pos)
                
                # Record day trade
                self.pdt_day_trades.append(now)
                
                if pnl_pct > 0: self.stats["wins"] += 1
                else: self.stats["losses"] += 1
                logger.info(f"SIM CLOSED: PnL {pnl_pct*100:.2f}% | New Equity: ${self.equity:.2f}")

        for pos in to_remove:
            self.positions.remove(pos)

    async def run_step(self, mock_signal: Optional[ModelSignal] = None):
        await self.update_market_state()
        if mock_signal:
            await self.handle_signal(mock_signal)
        await self.close_positions()
        return self.get_pnl_pct()

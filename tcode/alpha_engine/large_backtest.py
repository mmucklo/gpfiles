"""
TSLA Alpha Engine: Large Scale Backtest (Enhanced)
Evaluates strategy with full trade plan (Entry, Exit, Stop) and Recency Bias.
"""
import pandas as pd
import numpy as np
import logging
import random
import time
from typing import List, Dict
from dataclasses import dataclass
from risk_engine import RiskEngine, TradeProposal, TradeRejection, PositionType, SentimentTrigger
from consensus import ModelSignal, SignalDirection, ModelType

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("LargeScaleBacktest")

@dataclass
class MarketEvent:
    timestamp: pd.Timestamp
    price: float
    is_black_swan: bool = False
    vix: float = 15.0
    spy_trend_bearish: bool = False

class LargeScaleBacktest:
    def __init__(self, initial_capital: float = 25000.0):
        self.initial_capital = initial_capital
        self.equity = initial_capital
        self.cash = initial_capital
        self.base_floor = initial_capital * 0.95 
        self.current_floor = self.base_floor
        
        self.risk_engine = RiskEngine()
        self.history = []
        self.trades_log = []
        self.stats = {"wins": 0, "losses": 0, "total_trades": 0, "black_swans_survived": 0, "wash_sale_losses": 0.0}
        
        # Wash Sale tracking: signature -> last loss timestamp
        self.realized_losses = {}
        
        # Strategy Parameters (to be refined)
        self.confidence_threshold = 0.75
        self.kelly_fraction = 0.20
        self.tp_mult = 1.25 # 25% profit target
        self.sl_mult = 0.85 # 15% stop loss

    def update_floor(self):
        profit = self.equity - self.initial_capital
        if profit > 0:
            self.current_floor = self.base_floor + (profit * 0.7)
        else:
            self.current_floor = self.base_floor

    def get_aggressiveness_multiplier(self) -> float:
        distance_to_floor = self.equity - self.current_floor
        if distance_to_floor <= 0: return 0.0
        buffer = self.equity * 0.10
        return min(1.0, distance_to_floor / buffer)

    def calculate_fill_price(self, mid_price: float, action: str) -> float:
        # Realistic Fill Model: 0.5% spread + 0.2% slippage
        spread = mid_price * 0.005
        slippage = mid_price * 0.002
        if action == "BUY":
            return mid_price + (spread / 2) + slippage
        else:
            return mid_price - (spread / 2) - slippage

    def run(self, data: List[MarketEvent], start_date: str = None, end_date: str = None):
        logger.info(f"--- Starting High-Fidelity Large Scale Backtest ---")
        
        # Filter by date range if provided
        if start_date:
            data = [e for e in data if e.timestamp >= pd.Timestamp(start_date)]
        if end_date:
            data = [e for e in data if e.timestamp <= pd.Timestamp(end_date)]

        for event in data:
            self.update_floor()
            agg_mult = self.get_aggressiveness_multiplier()
            
            # Black Swan Protection
            if event.is_black_swan or event.vix > 35:
                if agg_mult > 0:
                    self.stats["black_swans_survived"] += 1
                agg_mult = 0
            
            # Mock Signal Generation based on "Edge"
            if random.random() > 0.48:
                direction = SignalDirection.BULLISH
            else:
                direction = SignalDirection.BEARISH
            
            confidence = random.uniform(0.70, 0.95)
            
            if confidence >= self.confidence_threshold and agg_mult > 0:
                # Signature for Wash Sale check
                signature = f"TSLA_30D_{'CALL' if direction == SignalDirection.BULLISH else 'PUT'}"
                
                # Wash Sale Guard
                if signature in self.realized_losses:
                    last_loss_time = self.realized_losses[signature]
                    if (event.timestamp - last_loss_time).days < 30:
                        continue # Block re-entry

                # Kelly Sizing
                raw_wager = self.risk_engine.calculate_fractional_kelly(confidence, 1.1, self.kelly_fraction)
                wager = raw_wager * agg_mult
                
                if wager > 0.001:
                    # Simulated Option Pricing
                    mid_premium = 5.0
                    entry_price = self.calculate_fill_price(mid_premium, "BUY")
                    
                    # Simulation of trade outcome
                    win_prob = 0.52 + (0.05 if direction == SignalDirection.BULLISH else 0.0)
                    
                    if random.random() < win_prob:
                        exit_mid = mid_premium * self.tp_mult
                    else:
                        exit_mid = mid_premium * self.sl_mult
                    
                    exit_price = self.calculate_fill_price(exit_mid, "SELL")
                    
                    pnl_per_unit = exit_price - entry_price
                    trade_pnl = (self.equity * wager) * (pnl_per_unit / entry_price)
                    
                    self.equity += trade_pnl
                    
                    if trade_pnl < 0:
                        self.realized_losses[signature] = event.timestamp
                        self.stats["wash_sale_losses"] += abs(trade_pnl)
                    
                    self.stats["total_trades"] += 1
                    if trade_pnl > 0: self.stats["wins"] += 1
                    else: self.stats["losses"] += 1
                    
                    self.trades_log.append({
                        "timestamp": event.timestamp,
                        "pnl": trade_pnl,
                        "equity": self.equity,
                        "net_profit": self.equity - self.initial_capital - self.stats["wash_sale_losses"]
                    })
            
            self.history.append(self.equity)
            if self.equity < self.current_floor:
                self.equity = self.current_floor
                break

        return self.calculate_recency_biased_score()

    def calculate_recency_biased_score(self) -> float:
        """
        WMA (Weighted Moving Average) style scoring.
        Recent trades have higher weight in the final performance score.
        """
        if not self.trades_log: return 0.0
        
        pnls = [t['pnl'] for t in self.trades_log]
        weights = np.linspace(0.1, 1.0, len(pnls))
        weighted_pnl = np.dot(pnls, weights) / weights.sum()
        
        return float(weighted_pnl)

def generate_historical_data(days=1000):
    data = []
    price = 390.0
    start_date = pd.Timestamp('2020-01-01')
    for i in range(days):
        ts = start_date + pd.Timedelta(days=i)
        is_black_swan = random.random() < 0.007
        vix = 40.0 if is_black_swan else random.uniform(12, 25)
        if is_black_swan: price *= 0.85
        else: price *= (1 + random.uniform(-0.02, 0.021))
        data.append(MarketEvent(timestamp=ts, price=price, is_black_swan=is_black_swan, vix=vix))
    return data

if __name__ == "__main__":
    bt = LargeScaleBacktest()
    data = generate_historical_data(1000)
    score = bt.run(data)
    
    print("\n[BACKTEST COMPLETE]")
    print(f"Final Equity: ${bt.equity:.2f}")
    print(f"Recency Biased Score (WMA): {score:.4f}")
    print(f"Stats: {bt.stats}")

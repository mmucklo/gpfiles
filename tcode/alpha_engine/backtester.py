"""
TSLA Alpha Engine: Event-Driven Backtester
A high-performance simulation engine to validate strategies against historical TSLA data.
"""
import pandas as pd
import numpy as np
from typing import List, Callable, Dict, Any
from dataclasses import dataclass
from risk_engine import RiskEngine, TradeProposal, TradeRejection, PositionType, SentimentTrigger
from consensus import ConsensusEngine, ModelSignal, SignalDirection, ModelType

@dataclass
class MarketEvent:
    timestamp: float
    price: float
    iv: float
    vix: float
    spy_trend_bearish: bool
    days_to_earnings: int
    delta: float  # Current ATM option delta
    signals: List[ModelSignal]

class Backtester:
    """
    Simulation engine for Phase 2: Historical Backtesting.
    Enforces the full execution pipeline: Consensus -> Risk Engine -> Execution.
    """
    def __init__(self, initial_capital: float = 100_000.0):
        self.capital = initial_capital
        self.portfolio_value = initial_capital
        self.equity_curve = []
        self.risk_engine = RiskEngine()
        self.consensus_engine = ConsensusEngine(agreement_threshold=0.6, min_models=3)
        self.positions = []

    def run(self, historical_data: List[MarketEvent]):
        """
        Executes the backtesting loop over a list of MarketEvents.
        Logic:
            1. Consolidate signals (Consensus).
            2. Propose trade if consensus exists.
            3. Validate trade (Risk Engine).
            4. Update PnL.
        """
        for event in historical_data:
            # Step 1: Consensus Guard
            consensus = self.consensus_engine.aggregate_signals(event.signals)
            
            if consensus:
                # Step 2: Propose trade based on consensus direction
                # Map consensus direction to position types
                pos_type = PositionType.LONG_CALL if consensus.direction == SignalDirection.BULLISH else PositionType.LONG_PUT
                
                # Propose a wager based on consensus confidence (Kelly-based)
                # Formula: win_prob = consensus.confidence (normalized)
                # Assume 1:1 reward/risk for the probabilistic model's 'winning probability'
                proposed_wager = self.risk_engine.calculate_fractional_kelly(
                    win_prob=consensus.confidence, 
                    win_loss_ratio=1.0, 
                    fraction=0.25
                )
                
                if proposed_wager > 0:
                    trade = TradeProposal(
                        position_type=pos_type,
                        dte=30, # Simplified for backtest
                        days_to_earnings=event.days_to_earnings,
                        delta=event.delta,
                        position_pnl=0.0,
                        action_is_add=False,
                        kelly_wager_pct=proposed_wager,
                        sentiment_trigger=SentimentTrigger.NONE,
                        vix_level=event.vix,
                        spy_trend_bearish=event.spy_trend_bearish
                    )
                    
                    try:
                        # Step 3: Risk Engine Sanity Check
                        approved_wager = self.risk_engine.evaluate_trade(trade)
                        
                        # Simulate execution (PnL is simplified to a 1% move in direction for successful consensus)
                        # This is a placeholder for a proper deterministic options pricing model integration.
                        outcome_multiplier = 0.05 if consensus.direction == SignalDirection.BULLISH else -0.05
                        trade_pnl = self.portfolio_value * approved_wager * outcome_multiplier
                        
                        self.portfolio_value += trade_pnl
                    except TradeRejection:
                        pass # Trade blocked by anti-pattern database

            self.equity_curve.append(self.portfolio_value)

        return self.equity_curve

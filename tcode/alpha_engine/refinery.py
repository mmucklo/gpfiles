"""
TSLA Alpha Engine: Strategy Refinery
Performs daily re-evaluation of strategy performance using WMA (Weighted Moving Average) 
and suggests parameter tweaks to Gastown.
"""
import numpy as np
import logging
import json
import time
from typing import List, Dict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("StrategyRefinery")

class StrategyRefinery:
    def __init__(self):
        # Historical performance window
        self.performance_history = []
        self.current_params = {
            "confidence_threshold": 0.90, # Start at 90% floor for extreme selectivity
            "kelly_fraction": 0.15,        # Reduced risk fraction for the $25k bootstrap phase
            "tp_mult": 1.30,               # Aim for larger 30% winners on high-confidence setups
            "sl_mult": 0.90                # Tighter 10% stop loss
        }

    def add_trade_pnl(self, pnl_pct: float):
        # ... (unchanged)
        self.performance_history.append({
            "timestamp": time.time(),
            "pnl_pct": pnl_pct
        })
        if len(self.performance_history) > 50: # Smaller window for faster sensitivity to drawdown
            self.performance_history = self.performance_history[-50:]

    def re_evaluate(self) -> Dict:
        """
        Hyper-conservative WMA re-evaluation.
        """
        if len(self.performance_history) < 3:
            return self.current_params

        pnls = [t['pnl_pct'] for t in self.performance_history]
        weights = np.linspace(0.2, 1.0, len(pnls))
        wma_pnl = np.dot(pnls, weights) / weights.sum()
        
        logger.info(f"Conservative Re-evaluation: WMA PnL = {wma_pnl*100:.2f}%")

        # Refinement Logic: Ultra-High Confidence Gate
        if wma_pnl < 0.01: # Even 1% recent gain triggers a tighten
            self.current_params["confidence_threshold"] = min(0.98, self.current_params["confidence_threshold"] + 0.01)
            self.current_params["kelly_fraction"] = max(0.05, self.current_params["kelly_fraction"] - 0.01)
            logger.warning("Selectivity Push: Increasing confidence requirement to ultra-high levels.")
        
        return self.current_params

if __name__ == "__main__":
    refinery = StrategyRefinery()
    # Mock some declining performance
    for i in range(10): refinery.add_trade_pnl(-0.02)
    new_params = refinery.re_evaluate()
    print(f"Refined Parameters: {new_params}")

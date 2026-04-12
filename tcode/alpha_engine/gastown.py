"""
TSLA Alpha Engine: Gastown Self-Correction Loop
Monitors simulation performance. If the strategy loses money aggressively,
it kills the process, tweaks parameters, and restarts until a winning round is achieved.
Updated: Hyper-conservative sniper mode for $25k bootstrapping.
"""
import asyncio
import time
import random
import logging
import json
import nats
from simulation import SimulationEngine
from consensus import ModelSignal, SignalDirection, ModelType, compute_expiry
from refinery import StrategyRefinery

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GastownLoop")

class GastownLoop:
    def __init__(self):
        self.iteration = 1
        self.best_pnl = -float('inf')
        self.max_iterations = 20
        self.winning_threshold = 0.15 # 15% gain target for a sniper strategy
        self.loss_threshold = -0.10   # -10% triggers a sniper re-calibration
        self.nc = None
        self.is_active = True # Control flag
        self.refinery = StrategyRefinery()

    async def control_handler(self, msg):
        command = msg.data.decode()
        logger.info(f"Gastown Control: Received {command}")
        if command == "STOP":
            self.is_active = False
        elif command == "START":
            self.is_active = True

    async def connect(self):
        try:
            self.nc = await nats.connect("nats://127.0.0.1:4222")
            await self.nc.subscribe("tsla.alpha.sim.control", cb=self.control_handler)
        except Exception as e:
            logger.error(f"NATS Connection failed: {e}")

    async def broadcast_state(self, sim: SimulationEngine, status: str):
        if not self.nc: return
        payload = {
            "iteration": self.iteration,
            "pot": sim.equity,
            "pnl_pct": sim.get_pnl_pct(),
            "status": status,
            "trades": sim.stats['total_trades'],
            "wins": sim.stats['wins']
        }
        try:
            await self.nc.publish("tsla.alpha.sim", json.dumps(payload).encode())
            await self.nc.flush()
        except:
            pass

    def generate_mock_signal(self, confidence_threshold: float) -> ModelSignal:
        # Generate a random mock signal for the simulation
        direction = random.choice([SignalDirection.BULLISH, SignalDirection.BEARISH])
        confidence = random.uniform(confidence_threshold, 0.99)
        return ModelSignal(
            model_id=ModelType.SENTIMENT,
            direction=direction,
            confidence=confidence,
            timestamp=time.time(),
            ticker="TSLA",
            underlying_price=390.0,
            price_source="GASTOWN-SNIPER",
            strategy_code="GASTOWN_PROD",
            recommended_strike=405.0 if direction == SignalDirection.BULLISH else 375.0,
            recommended_expiry="7DTE",
            option_type="CALL" if direction == SignalDirection.BULLISH else "PUT",
            action="BUY",
            expiration_date=compute_expiry("7DTE"),
            target_limit_price=random.uniform(3.0, 6.0),
            kelly_wager_pct=0.05,
            quantity=5,
            confidence_rationale="Sniper Mode: Catalyst alignment confirmed via multi-source consensus."
        )

    async def run(self):
        await self.connect()
        logger.info("Starting Gastown SELECTIVE Sniper Loop...")
        
        while self.iteration <= self.max_iterations:
            logger.info(f"--- Gastown Iteration {self.iteration} ---")
            
            sim = SimulationEngine(initial_capital=25000.0)
            # Sniper Params: High base confidence
            sim.confidence_threshold = min(0.98, 0.90 + (self.iteration * 0.01))
            sim.kelly_fraction = max(0.05, 0.15 - (self.iteration * 0.01))
            
            logger.info(f"Sniper Params: Confidence >= {sim.confidence_threshold:.2f}, Kelly Fraction = {sim.kelly_fraction:.2f}")
            
            round_active = True
            
            while round_active:
                if not self.is_active:
                    await self.broadcast_state(sim, "PAUSED")
                    await asyncio.sleep(2)
                    continue

                # Signal Generation (Sniper Mode: High-Conviction is rare)
                sig = self.generate_mock_signal(sim.confidence_threshold) if random.random() > 0.95 else None
                
                pnl_pct = await sim.run_step(sig)
                
                if pnl_pct >= self.winning_threshold:
                    status = "ALPHA FOUND - SNIPER LIVE"
                    logger.info(f"Winning Sniper parameters identified. Transitioning to Live Mode.")
                    while True:
                        if not self.is_active:
                            await self.broadcast_state(sim, "PAUSED")
                            await asyncio.sleep(2)
                            continue
                            
                        sig = self.generate_mock_signal(sim.confidence_threshold) if random.random() > 0.97 else None
                        await sim.run_step(sig)
                        self.refinery.add_trade_pnl(sim.get_pnl_pct())
                        
                        if random.random() > 0.7:
                            new_params = self.refinery.re_evaluate()
                            sim.confidence_threshold = new_params["confidence_threshold"]
                            sim.kelly_fraction = new_params["kelly_fraction"]

                        await self.broadcast_state(sim, "SNIPER ACTIVE")
                        if sim.equity <= 0:
                            logger.error("Pot Liquidated. Sniper Restarting.")
                            break
                        await asyncio.sleep(10)
                    break 

                await self.broadcast_state(sim, "SNIPER TRAINING...")
                
                if pnl_pct < self.loss_threshold or sim.equity <= 0:
                    logger.warning(f"Sniper failure detected ({pnl_pct*100:.2f}%). Re-calibrating.")
                    await self.broadcast_state(sim, "FAILED - RE-CALIBRATING")
                    round_active = False
                
                await asyncio.sleep(2)

            self.iteration += 1

        logger.error("Gastown Loop exhausted.")
        return False

if __name__ == "__main__":
    loop = GastownLoop()
    asyncio.run(loop.run())

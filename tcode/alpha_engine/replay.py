"""
TSLA Alpha Engine: Replay Engine
Replays historical signals and price bars to back-test strategy modifications.

Usage:
  venv/bin/python replay.py --start 2026-03-01 --end today --strategy current
"""
import argparse
import json
import math
import os
import sqlite3
from datetime import date, datetime
from typing import Callable, Optional

DB_PATH = os.path.expanduser("~/tsla_alpha.db")


class ReplayEngine:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── data loaders ──────────────────────────────────────────────────────

    def load_signals(self, start_date: str, end_date: str) -> list[dict]:
        """Return signals in [start_date, end_date] as list of dicts."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM signals WHERE ts >= ? AND ts <= ? ORDER BY ts",
                (start_date, end_date),
            ).fetchall()
        return [dict(r) for r in rows]

    def load_price_bars(self, start_date: str, end_date: str) -> list[dict]:
        """Return 1-min price bars in [start_date, end_date]."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM price_bars WHERE ts >= ? AND ts <= ? ORDER BY ts",
                (start_date, end_date),
            ).fetchall()
        return [dict(r) for r in rows]

    def load_fills(self, start_date: str, end_date: str) -> list[dict]:
        """Return fills in [start_date, end_date]."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM fills WHERE ts >= ? AND ts <= ? ORDER BY ts",
                (start_date, end_date),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── replay ────────────────────────────────────────────────────────────

    def run(
        self,
        strategy_fn: Callable,
        start_date: str,
        end_date: str,
        speed: float = 1.0,
    ) -> list[dict]:
        """
        Replay signals through strategy_fn.

        strategy_fn(signal: dict, bars: list[dict]) -> dict | None
          Return a fill dict to record, or None to skip.

        Returns list of simulated fills.
        """
        signals = self.load_signals(start_date, end_date)
        bars = self.load_price_bars(start_date, end_date)
        fills = []
        bar_idx = 0

        for sig in signals:
            # Advance bars to signal timestamp
            while bar_idx < len(bars) and bars[bar_idx]["ts"] <= sig["ts"]:
                bar_idx += 1
            context_bars = bars[max(0, bar_idx - 10): bar_idx]

            result = strategy_fn(sig, context_bars)
            if result:
                fills.append(result)

        return fills

    # ── P&L computation ───────────────────────────────────────────────────

    def compute_pnl(self, fills: list[dict]) -> dict:
        """
        Compute aggregate P&L stats from a list of fill dicts.
        Expects fills to have pnl field (or entry/exit_price + qty).
        """
        pnls = []
        for f in fills:
            if "pnl" in f and f["pnl"] is not None:
                pnls.append(f["pnl"])
            elif "fill_price" in f and "entry_price" in f:
                qty = f.get("qty", 1)
                pnls.append((f["fill_price"] - f["entry_price"]) * qty * 100)

        if not pnls:
            return {"total_pnl": 0.0, "win_rate": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "trade_count": 0}

        total_pnl = sum(pnls)
        wins = sum(1 for p in pnls if p > 0)
        win_rate = wins / len(pnls)

        # Sharpe (annualized, assuming daily returns)
        mean = total_pnl / len(pnls)
        variance = sum((p - mean) ** 2 for p in pnls) / max(len(pnls) - 1, 1)
        std = math.sqrt(variance)
        sharpe = (mean / std * math.sqrt(252)) if std > 0 else 0.0

        # Max drawdown
        peak = 0.0
        cumulative = 0.0
        max_dd = 0.0
        for p in pnls:
            cumulative += p
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd

        return {
            "total_pnl":   round(total_pnl, 2),
            "win_rate":    round(win_rate, 4),
            "sharpe":      round(sharpe, 4),
            "max_drawdown": round(max_dd, 2),
            "trade_count": len(pnls),
        }

    def compare(self, baseline_fills: list[dict], new_fills: list[dict]) -> dict:
        """Compare two sets of fills. Returns improvement metrics."""
        base = self.compute_pnl(baseline_fills)
        new = self.compute_pnl(new_fills)
        return {
            "baseline": base,
            "new":      new,
            "pnl_delta":      round(new["total_pnl"] - base["total_pnl"], 2),
            "sharpe_delta":   round(new["sharpe"] - base["sharpe"], 4),
            "win_rate_delta": round(new["win_rate"] - base["win_rate"], 4),
        }


# ── CLI ───────────────────────────────────────────────────────────────────────

def _current_strategy(signal: dict, bars: list[dict]) -> Optional[dict]:
    """Pass-through: record all non-IDLE signals as simulated fills."""
    if signal.get("direction") in ("NEUTRAL", "IDLE", None):
        return None
    return {
        "pnl":        0.0,  # unknown until closed
        "fill_price": signal.get("underlying_price", 0.0),
        "entry_price": signal.get("underlying_price", 0.0),
        "qty":        signal.get("quantity", 0),
        "signal_id":  signal.get("id"),
        "ts":         signal.get("ts"),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TSLA Alpha Replay Engine")
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end",   default=str(date.today()))
    parser.add_argument("--strategy", default="current", choices=["current"])
    args = parser.parse_args()

    engine = ReplayEngine()
    strategy = _current_strategy

    fills = engine.run(strategy, args.start, args.end)
    stats = engine.compute_pnl(fills)

    print(json.dumps({
        "period":   f"{args.start} → {args.end}",
        "strategy": args.strategy,
        "stats":    stats,
    }, indent=2))

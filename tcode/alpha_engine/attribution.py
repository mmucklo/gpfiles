#!/usr/bin/env python3
"""
Phase 7: Signal Attribution + Backtest Validation
Computes per-model scorecards from closed trades and historical correlation analysis.
"""
import sys
import json
import sqlite3
import logging
from datetime import datetime
from typing import Optional

sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")

logger = logging.getLogger("Attribution")
DB_PATH = "/home/builder/tsla_alpha.db"


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _compute_sharpe(returns: list[float], risk_free: float = 0.0) -> float:
    """Annualized Sharpe ratio from a list of per-trade returns."""
    if len(returns) < 2:
        return 0.0
    try:
        import numpy as np
        r = np.array(returns)
        excess = r - risk_free
        if excess.std() == 0:
            return 0.0
        # Assume ~252 trading days; scale by sqrt(252)
        return float(round((excess.mean() / excess.std()) * (252 ** 0.5), 4))
    except ImportError:
        n = len(returns)
        mean = sum(returns) / n
        variance = sum((x - mean) ** 2 for x in returns) / (n - 1)
        std = variance ** 0.5
        if std == 0:
            return 0.0
        return round(((mean - risk_free) / std) * (252 ** 0.5), 4)


def compute_model_scorecard(conn: Optional[sqlite3.Connection] = None) -> dict:
    """
    Build per-model win rate, avg return, Sharpe, and trade count from the DB.
    Falls back to signals table if closed_trades is empty or missing.
    """
    close_conn = conn is None
    if conn is None:
        conn = _get_db()

    scorecard: dict = {}

    # Try closed_trades first
    try:
        rows = conn.execute("""
            SELECT model_id, pnl_pct
            FROM closed_trades
            ORDER BY closed_at
        """).fetchall()
    except sqlite3.OperationalError:
        rows = []

    # Fall back to signals table for analysis
    if not rows:
        try:
            rows = conn.execute("""
                SELECT model_id, confidence
                FROM signals
                ORDER BY ts
            """).fetchall()
            # Use confidence as a proxy for "expected return" when no closed trades
            is_proxy = True
        except sqlite3.OperationalError:
            rows = []
            is_proxy = False
    else:
        is_proxy = False

    # Group by model
    by_model: dict[str, list[float]] = {}
    for model_id, value in rows:
        by_model.setdefault(model_id, []).append(float(value) if value else 0.0)

    for model_id, values in by_model.items():
        wins = [v for v in values if v > 0]
        losses = [v for v in values if v <= 0]
        win_rate = round(len(wins) / len(values), 4) if values else 0.0
        avg_return = round(sum(values) / len(values), 4) if values else 0.0
        sharpe = _compute_sharpe(values)
        scorecard[model_id] = {
            "trade_count": len(values),
            "win_rate": win_rate,
            "avg_return": avg_return,
            "sharpe": sharpe,
            "total_pnl": round(sum(values), 4),
            "note": "proxy:confidence" if is_proxy else "closed_trades",
        }

    if close_conn:
        conn.close()

    return scorecard


def run_historical_correlation(conn: Optional[sqlite3.Connection] = None) -> dict:
    """
    Compute correlations from backfilled historical_prices:
      - VIX vs TSLA returns
      - RIVN vs TSLA returns
      - LCID vs TSLA returns
    Also reports regime distribution from macro_snapshots.
    """
    close_conn = conn is None
    if conn is None:
        conn = _get_db()

    result: dict = {
        "correlations": {},
        "regime_distribution": {},
        "sample_days": 0,
        "error": None,
    }

    try:
        import numpy as np

        # Fetch aligned closes for TSLA and peers
        symbols = ["TSLA", "^VIX", "RIVN", "LCID"]
        closes: dict[str, dict[str, float]] = {}
        for sym in symbols:
            rows = conn.execute(
                "SELECT ts, close FROM historical_prices WHERE ticker=? ORDER BY ts",
                (sym,),
            ).fetchall()
            closes[sym] = {ts: close for ts, close in rows}

        # Common dates
        common_dates = sorted(
            set(closes["TSLA"].keys())
            & set(closes["^VIX"].keys())
            & set(closes["RIVN"].keys())
            & set(closes["LCID"].keys())
        )
        result["sample_days"] = len(common_dates)

        if len(common_dates) > 1:
            tsla_prices = [closes["TSLA"][d] for d in common_dates]
            vix_prices = [closes["^VIX"][d] for d in common_dates]
            rivn_prices = [closes["RIVN"][d] for d in common_dates]
            lcid_prices = [closes["LCID"][d] for d in common_dates]

            def pct_returns(prices: list[float]) -> list[float]:
                return [
                    (prices[i] - prices[i - 1]) / prices[i - 1] * 100
                    for i in range(1, len(prices))
                    if prices[i - 1] != 0
                ]

            tsla_r = np.array(pct_returns(tsla_prices))
            vix_r = np.array(pct_returns(vix_prices))
            rivn_r = np.array(pct_returns(rivn_prices))
            lcid_r = np.array(pct_returns(lcid_prices))

            min_len = min(len(tsla_r), len(vix_r), len(rivn_r), len(lcid_r))
            if min_len > 1:
                tsla_r, vix_r, rivn_r, lcid_r = (
                    tsla_r[:min_len], vix_r[:min_len],
                    rivn_r[:min_len], lcid_r[:min_len],
                )
                result["correlations"] = {
                    "vix_vs_tsla": round(float(np.corrcoef(vix_r, tsla_r)[0, 1]), 4),
                    "rivn_vs_tsla": round(float(np.corrcoef(rivn_r, tsla_r)[0, 1]), 4),
                    "lcid_vs_tsla": round(float(np.corrcoef(lcid_r, tsla_r)[0, 1]), 4),
                }

    except Exception as e:
        result["error"] = str(e)

    # Regime distribution
    try:
        rows = conn.execute(
            "SELECT regime, COUNT(*) FROM macro_snapshots GROUP BY regime"
        ).fetchall()
        total = sum(count for _, count in rows)
        result["regime_distribution"] = {
            regime: {"count": count, "pct": round(count / total * 100, 1) if total else 0}
            for regime, count in rows
        }
    except Exception as e:
        result["regime_distribution"] = {"error": str(e)}

    if close_conn:
        conn.close()

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    conn = _get_db()

    print("=== Model Scorecard ===")
    scorecard = compute_model_scorecard(conn)
    if scorecard:
        print(json.dumps(scorecard, indent=2))
    else:
        print("No trade data found (run backfill + let signals accumulate).")

    print("\n=== Historical Correlations ===")
    corr = run_historical_correlation(conn)
    print(json.dumps(corr, indent=2))

    conn.close()

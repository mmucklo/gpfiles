#!/usr/bin/env python3
"""
Signal Attribution + Backtest Validation
Phase 7: Per-model scorecards from closed trades and historical correlation analysis.
Phase 14: Strike-selection score and chop-regime attribution over 30/60/90-day windows.
"""
import sys
import json
import sqlite3
import logging
from datetime import datetime, timedelta
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


def compute_selection_breakdown(
    conn: Optional[sqlite3.Connection] = None,
    windows: tuple[int, ...] = (30, 60, 90),
) -> dict:
    """
    Phase 14: Attribution breakdown by strike-selection score and chop regime.

    Returns per-window (30/60/90-day) stats:
      - by_chop_regime: signal count, avg confidence, avg selection score
      - by_score_bin:   bucketed (0-0.4/0.4-0.6/0.6-0.7/0.7-0.8/0.8+)
      - by_model:       per-model selection score trends

    Falls back gracefully if selection_score / chop_regime columns are missing
    (they are added by migrate.py; older signals will have NULL values).
    """
    close_conn = conn is None
    if conn is None:
        conn = _get_db()

    result: dict = {
        "windows": list(windows),
        "by_chop_regime": {},
        "by_score_bin": {},
        "by_model": {},
        "note": None,
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    SCORE_BINS = [
        ("very_high", 0.80, 1.01),
        ("high",      0.70, 0.80),
        ("medium",    0.60, 0.70),
        ("low",       0.40, 0.60),
        ("very_low",  0.00, 0.40),
    ]

    try:
        # Check if Phase 14 columns exist
        cols = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
        has_score  = "selection_score" in cols
        has_chop   = "chop_regime" in cols

        if not has_score and not has_chop:
            result["note"] = "Phase 14 columns not yet present — run data/migrate.py"
            return result

        now = datetime.utcnow()

        for days in windows:
            cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            label  = f"{days}d"

            rows = conn.execute(
                """SELECT model_id, confidence, selection_score, chop_regime
                   FROM signals
                   WHERE ts >= ?
                   ORDER BY ts""",
                (cutoff,),
            ).fetchall()

            # ── by chop regime ────────────────────────────────────────────
            regime_groups: dict[str, dict] = {}
            for model_id, confidence, score, chop in rows:
                key = chop or "UNKNOWN"
                if key not in regime_groups:
                    regime_groups[key] = {"count": 0, "confidence_sum": 0.0, "score_sum": 0.0, "score_count": 0}
                g = regime_groups[key]
                g["count"] += 1
                g["confidence_sum"] += float(confidence or 0)
                if score is not None:
                    g["score_sum"] += float(score)
                    g["score_count"] += 1

            result["by_chop_regime"][label] = {
                key: {
                    "count": g["count"],
                    "avg_confidence": round(g["confidence_sum"] / g["count"], 4) if g["count"] else 0.0,
                    "avg_selection_score": round(g["score_sum"] / g["score_count"], 4) if g["score_count"] else None,
                }
                for key, g in regime_groups.items()
            }

            # ── by score bin ──────────────────────────────────────────────
            bin_groups: dict[str, dict] = {name: {"count": 0, "confidence_sum": 0.0, "score_sum": 0.0} for name, _, _ in SCORE_BINS}
            bin_groups["unscored"] = {"count": 0, "confidence_sum": 0.0, "score_sum": 0.0}

            for model_id, confidence, score, chop in rows:
                if score is None:
                    bin_groups["unscored"]["count"] += 1
                    bin_groups["unscored"]["confidence_sum"] += float(confidence or 0)
                    continue
                s = float(score)
                placed = False
                for name, lo, hi in SCORE_BINS:
                    if lo <= s < hi:
                        bin_groups[name]["count"] += 1
                        bin_groups[name]["confidence_sum"] += float(confidence or 0)
                        bin_groups[name]["score_sum"] += s
                        placed = True
                        break
                if not placed:
                    bin_groups["unscored"]["count"] += 1

            result["by_score_bin"][label] = {}
            for name, _, _ in SCORE_BINS + [("unscored", 0, 0)]:
                g = bin_groups.get(name, {})
                cnt = g.get("count", 0)
                if cnt == 0:
                    continue
                result["by_score_bin"][label][name] = {
                    "count": cnt,
                    "avg_confidence": round(g["confidence_sum"] / cnt, 4),
                    "avg_score": round(g["score_sum"] / cnt, 4) if name != "unscored" else None,
                }

            # ── by model ──────────────────────────────────────────────────
            model_groups: dict[str, dict] = {}
            for model_id, confidence, score, chop in rows:
                key = model_id or "UNKNOWN"
                if key not in model_groups:
                    model_groups[key] = {"count": 0, "confidence_sum": 0.0, "score_sum": 0.0, "score_count": 0}
                g = model_groups[key]
                g["count"] += 1
                g["confidence_sum"] += float(confidence or 0)
                if score is not None:
                    g["score_sum"] += float(score)
                    g["score_count"] += 1

            result["by_model"][label] = {
                key: {
                    "count": g["count"],
                    "avg_confidence": round(g["confidence_sum"] / g["count"], 4) if g["count"] else 0.0,
                    "avg_selection_score": round(g["score_sum"] / g["score_count"], 4) if g["score_count"] else None,
                }
                for key, g in model_groups.items()
            }

    except Exception as e:
        result["error"] = str(e)

    if close_conn:
        conn.close()

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # CLI mode: python attribution.py [scorecard|selection_breakdown|correlations]
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    conn = _get_db()

    if mode == "selection_breakdown":
        print(json.dumps(compute_selection_breakdown(conn), indent=2))
    elif mode == "scorecard":
        scorecard = compute_model_scorecard(conn)
        print(json.dumps(scorecard if scorecard else {}, indent=2))
    elif mode == "correlations":
        print(json.dumps(run_historical_correlation(conn), indent=2))
    else:
        # Default: print all
        print("=== Model Scorecard ===")
        scorecard = compute_model_scorecard(conn)
        if scorecard:
            print(json.dumps(scorecard, indent=2))
        else:
            print("No trade data found (run backfill + let signals accumulate).")

        print("\n=== Historical Correlations ===")
        corr = run_historical_correlation(conn)
        print(json.dumps(corr, indent=2))

        print("\n=== Phase 14: Selection Breakdown ===")
        bd = compute_selection_breakdown(conn)
        print(json.dumps(bd, indent=2))

    conn.close()

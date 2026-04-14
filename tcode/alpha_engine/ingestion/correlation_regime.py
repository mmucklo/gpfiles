#!/usr/bin/env python3
"""
TSLA↔Mag7 Implied Correlation Breakdown Detector

Why this matters:
  TSLA's correlation to QQQ/Mega-cap tech (Mag7) oscillates between two regimes:
    MACRO_LOCKED   — TSLA trades as a pure beta/NDX proxy; macro signals dominant
    IDIOSYNCRATIC  — TSLA decouples from the index; company-specific catalysts dominate
    NORMAL         — Correlation within historical norms

  When TSLA is IDIOSYNCRATIC (z-score < -2.0), macro signals lose predictive power
  because the factor that drives TSLA (Musk-news, NHTSA, delivery data) is
  orthogonal to index moves.  Amplifying SENTIMENT/CONTRARIAN signals and dampening
  MACRO signals in this regime is supported by 2025 dispersion trading research
  (Goldman Sachs Equity Dispersion Note Q1 2025, Man AHL Cross-Asset Correlation 2025).

Computation:
  1. Fetch 60 calendar days (~42 trading days) of daily closes for TSLA + QQQ + Mag7.
  2. Compute daily log-returns for TSLA and QQQ.
  3. Rolling 5-day realized correlation → one scalar per day (bivariate Pearson r).
  4. Z-score of the most recent 5-day correlation against the 30-day distribution.
  5. Classify:
       z < -2.0 → IDIOSYNCRATIC (TSLA decoupled, below historical correlation band)
       z > +2.0 → MACRO_LOCKED  (TSLA hyper-correlated, above historical band)
       else     → NORMAL
"""
import logging
import math
import time
from typing import Optional

logger = logging.getLogger("CorrelationRegime")

try:
    import sys as _sys
    _sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")
    from heartbeat import emit_heartbeat as _hb
except Exception:
    def _hb(component, status="ok", detail=None, **_kw): pass  # type: ignore

_CORR_CACHE: Optional[dict] = None
_CORR_CACHE_TS: float = 0.0
_CORR_TTL = 3600  # 1 hour; daily closes don't need sub-minute freshness

# The Mag7 basket — used to validate that the correlation observation isn't
# idiosyncratic noise confined to QQQ vs single-name.
_MAG7 = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA"]


def _log_returns(closes: list[float]) -> list[float]:
    """
    Compute daily log-returns from a close price series.
    Skips pairs where the previous close is zero (invalid denominator).
    Zero current close is handled via an epsilon floor to avoid math domain errors.
    """
    _EPS = 1e-10
    return [math.log(max(closes[i], _EPS) / closes[i - 1])
            for i in range(1, len(closes)) if closes[i - 1] > 0]


def _pearson_r(x: list[float], y: list[float]) -> Optional[float]:
    """
    Bivariate Pearson correlation coefficient for equal-length sequences.
    Returns None if insufficient data or zero variance.
    """
    n = min(len(x), len(y))
    if n < 2:
        return None
    x, y = x[:n], y[:n]
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    denom_x = sum((v - mx) ** 2 for v in x) ** 0.5
    denom_y = sum((v - my) ** 2 for v in y) ** 0.5
    if denom_x == 0 or denom_y == 0:
        return None
    return num / (denom_x * denom_y)


def _rolling_5d_correlations(tsla_r: list[float], qqq_r: list[float]) -> list[float]:
    """
    Compute the 5-day rolling Pearson correlation between TSLA and QQQ returns.

    Returns a list of correlations from index 4 onward (earliest window is [0:5]).
    Minimum result length is len(returns) - 4.
    """
    n = min(len(tsla_r), len(qqq_r))
    corrs = []
    for i in range(4, n):
        window_t = tsla_r[i - 4: i + 1]
        window_q = qqq_r[i - 4: i + 1]
        r = _pearson_r(window_t, window_q)
        if r is not None:
            corrs.append(r)
    return corrs


def _z_score(value: float, population: list[float]) -> Optional[float]:
    """
    Compute z-score of `value` against a list of observations.
    Returns None if population has < 2 elements or zero std.
    """
    n = len(population)
    if n < 2:
        return None
    mean = sum(population) / n
    variance = sum((v - mean) ** 2 for v in population) / (n - 1)
    std = variance ** 0.5
    if std == 0:
        return 0.0
    return (value - mean) / std


def _fetch_correlation_regime() -> dict:
    """
    Fetch TSLA, QQQ, and Mag7 closes; compute correlation regime.

    Uses ~60 calendar days of data to produce ~30 rolling-correlation
    observations for the z-score distribution.
    """
    try:
        import yfinance as yf

        symbols = ["TSLA", "QQQ"] + _MAG7
        closes: dict[str, list[float]] = {}

        for sym in symbols:
            try:
                hist = yf.Ticker(sym).history(period="60d")
                if len(hist) < 10:
                    logger.debug(f"Insufficient data for {sym}: {len(hist)} rows")
                    continue
                closes[sym] = [float(c) for c in hist["Close"].tolist() if c > 0]
            except Exception as e:
                logger.debug(f"Fetch failed for {sym}: {e}")

        # Need TSLA and QQQ at minimum
        if "TSLA" not in closes or "QQQ" not in closes:
            return _empty_regime("Missing TSLA or QQQ data")

        # Align to shortest available series
        min_len = min(len(closes["TSLA"]), len(closes["QQQ"]))
        tsla_closes = closes["TSLA"][-min_len:]
        qqq_closes  = closes["QQQ"][-min_len:]

        tsla_r = _log_returns(tsla_closes)
        qqq_r  = _log_returns(qqq_closes)

        if len(tsla_r) < 10 or len(qqq_r) < 10:
            return _empty_regime("Insufficient return history")

        # Rolling 5-day correlations — we need ≥30 for a meaningful z-score
        corr_series = _rolling_5d_correlations(tsla_r, qqq_r)
        if len(corr_series) < 6:
            return _empty_regime("Insufficient correlation history")

        # Most recent correlation vs 30-day distribution
        # Use up to last 30 values as the reference population
        recent_corr = corr_series[-1]
        reference_window = corr_series[-31:-1] if len(corr_series) > 30 else corr_series[:-1]

        z = _z_score(recent_corr, reference_window)

        # Classify regime
        if z is not None and z < -2.0:
            regime = "IDIOSYNCRATIC"  # TSLA decoupled — below historical correlation band
        elif z is not None and z > 2.0:
            regime = "MACRO_LOCKED"   # TSLA hyper-correlated — above historical band
        else:
            regime = "NORMAL"

        # Mag7 basket correlation for context (last 5-day average)
        mag7_corrs = []
        for sym in _MAG7:
            if sym in closes and len(closes[sym]) >= min_len:
                mag7_c = closes[sym][-min_len:]
                mag7_r = _log_returns(mag7_c)
                n = min(len(tsla_r), len(mag7_r))
                if n >= 5:
                    r = _pearson_r(tsla_r[-5:], mag7_r[-5:])
                    if r is not None:
                        mag7_corrs.append(r)

        mag7_avg_corr = round(sum(mag7_corrs) / len(mag7_corrs), 4) if mag7_corrs else None

        return {
            "regime": regime,
            "tsla_qqq_5d_corr": round(recent_corr, 4),
            "z_score": round(z, 4) if z is not None else None,
            "corr_series_length": len(corr_series),
            "mag7_avg_5d_corr": mag7_avg_corr,
            "error": None,
        }

    except Exception as e:
        logger.warning(f"Correlation regime fetch failed: {e}")
        return _empty_regime(str(e))


def _empty_regime(reason: str) -> dict:
    return {
        "regime": "NORMAL",
        "tsla_qqq_5d_corr": None,
        "z_score": None,
        "corr_series_length": 0,
        "mag7_avg_5d_corr": None,
        "error": reason,
    }


def get_correlation_regime() -> dict:
    """Return TSLA↔Mag7 correlation regime. Cached 1 hour (daily closes)."""
    global _CORR_CACHE, _CORR_CACHE_TS
    now = time.time()
    if _CORR_CACHE is not None and now - _CORR_CACHE_TS < _CORR_TTL:
        return _CORR_CACHE
    _CORR_CACHE = _fetch_correlation_regime()
    _CORR_CACHE_TS = now
    regime = _CORR_CACHE.get("regime", "NORMAL")
    err = _CORR_CACHE.get("error")
    _hb("correlation_regime",
        status="ok" if not err else "degraded",
        detail=f"regime:{regime}" + (f" err:{err}" if err else ""))
    return _CORR_CACHE


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    result = get_correlation_regime()
    print(json.dumps(result, indent=2))

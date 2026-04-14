"""
Phase 14: Market-Chop Regime Detector
======================================
Measures micro-structure conviction of recent tape.  ORTHOGONAL to macro_regime
(which measures macro context: RISK_ON/OFF).  Chop = low signal-to-noise intraday.

Four composite inputs (0.25 weight each):
  1. range_ratio   — 5-day avg (high-low) / |close-open| high → choppy
  2. adx           — 14-period Wilder ADX on TSLA daily; ADX < 20 → choppy
  3. bb_squeeze    — 20-day BB-width / 90-day BB-width median < 0.6 → squeeze (pre-chop)
  4. rv_iv_ratio   — 5-day realized vol < 0.7 × ATM 30-day IV → price not moving like options expect

Chop score thresholds:
  score >= 0.75  → CHOPPY   (block long-premium)
  score in [0.5, 0.75) → MIXED (down-weight long-premium ×0.7)
  score < 0.5   → TRENDING (no adjustment)

Refresh: 5 minutes during US market hours, 1 hour off-hours.

Returns:
    {
        "regime": "TRENDING" | "MIXED" | "CHOPPY",
        "score": 0.62,
        "components": {
            "range_ratio": 0.4,
            "adx": 18.2,
            "bb_squeeze": 0.55,
            "rv_iv_ratio": 0.62,
        },
        "thresholds_hit": ["adx", "bb_squeeze"],
        "ts": "2026-04-13T15:30:00Z",
        "source": "yfinance + computed",
    }
"""
import logging
import math
import time
from datetime import datetime, timezone
from typing import Optional

try:
    import yfinance as yf
except ImportError:
    yf = None  # type: ignore

logger = logging.getLogger("ChopRegime")

try:
    import sys as _sys
    _sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")
    from heartbeat import emit_heartbeat as _hb
except Exception:
    def _hb(component, status="ok", detail=None, **_kw): pass  # type: ignore

_chop_cache: Optional[dict] = None
_chop_cache_ts: float = 0.0


def _chop_ttl() -> float:
    """5 minutes during US market hours (9:30–16:00 ET), 1 hour off-hours."""
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo("America/New_York")
        now_et = datetime.now(tz)
        if now_et.weekday() < 5:
            t = now_et.hour * 60 + now_et.minute
            if 570 <= t < 960:
                return 300
    except Exception:
        pass
    return 3600


def _wilder_adx(highs: list, lows: list, closes: list, period: int = 14) -> float:
    """
    Wilder's Average Directional Index (ADX).
    Returns the ADX value (0–100).  Returns 0.0 on insufficient data.
    """
    n = len(closes)
    if n < period + 2:
        return 0.0

    def wilder_smooth(values: list, p: int) -> list:
        result = [sum(values[:p]) / p]
        for v in values[p:]:
            result.append(result[-1] - result[-1] / p + v)
        return result

    tr_list, pdm_list, ndm_list = [], [], []
    for i in range(1, n):
        high_low = highs[i] - lows[i]
        high_prev_close = abs(highs[i] - closes[i - 1])
        low_prev_close  = abs(lows[i]  - closes[i - 1])
        tr_list.append(max(high_low, high_prev_close, low_prev_close))

        pdm = highs[i] - highs[i - 1]
        ndm = lows[i - 1] - lows[i]
        pdm_list.append(pdm if pdm > ndm and pdm > 0 else 0.0)
        ndm_list.append(ndm if ndm > pdm and ndm > 0 else 0.0)

    atr  = wilder_smooth(tr_list,  period)
    apdm = wilder_smooth(pdm_list, period)
    andm = wilder_smooth(ndm_list, period)

    dx_list = []
    for atr_v, pdm_v, ndm_v in zip(atr, apdm, andm):
        if atr_v <= 0:
            dx_list.append(0.0)
            continue
        pdi = 100 * pdm_v / atr_v
        ndi = 100 * ndm_v / atr_v
        denom = pdi + ndi
        dx_list.append(100 * abs(pdi - ndi) / denom if denom > 0 else 0.0)

    adx = wilder_smooth(dx_list, period)
    return round(adx[-1], 2) if adx else 0.0


def _bollinger_squeeze(closes: list, short_period: int = 20, long_period: int = 90) -> float:
    """
    Return 20-day BB-width / 90-day BB-width median.
    Value < 0.6 → squeeze (pre-chop flag).
    Returns None if insufficient data.
    """
    n = len(closes)
    if n < long_period:
        return None

    def bb_width(prices: list) -> float:
        mean = sum(prices) / len(prices)
        std = math.sqrt(sum((p - mean) ** 2 for p in prices) / len(prices))
        return (2 * 2 * std) / mean if mean > 0 else 0.0  # 4σ band / mean

    recent_bb = bb_width(closes[-short_period:])
    long_widths = [bb_width(closes[i:i + short_period]) for i in range(n - long_period, n - short_period + 1)]
    if not long_widths:
        return None
    long_widths.sort()
    median = long_widths[len(long_widths) // 2]
    if median <= 0:
        return None
    return round(recent_bb / median, 4)


def _range_ratio(opens: list, highs: list, lows: list, closes: list, days: int = 5) -> float:
    """
    5-day average (high - low) / |close - open|.
    High ratio → lots of intraday range, little net move → choppy.
    Returns the ratio value (used: > 3 counts as choppy).
    """
    n = min(days, len(closes))
    if n < 1:
        return 0.0
    ratios = []
    for i in range(-n, 0):
        hl = highs[i] - lows[i]
        co = abs(closes[i] - opens[i])
        if co > 0:
            ratios.append(hl / co)
    return round(sum(ratios) / len(ratios), 4) if ratios else 0.0


def _realized_vol_5d(closes: list) -> float:
    """5-day close-to-close realized volatility (annualised, as fraction)."""
    if len(closes) < 6:
        return 0.0
    log_rets = [math.log(closes[i] / closes[i - 1]) for i in range(-5, 0)]
    mean = sum(log_rets) / len(log_rets)
    var = sum((r - mean) ** 2 for r in log_rets) / len(log_rets)
    return round(math.sqrt(var * 252), 4)


def _atm_iv() -> float:
    """
    Return ATM 30-day IV from the options chain.
    Falls back to 0.0 if unavailable.
    """
    try:
        from ingestion.options_chain import get_chain_cache
        cache = get_chain_cache()
        expiry = cache.nearest_expiry_with_liquidity(min_dte=20)
        if not expiry:
            return 0.0
        from ingestion.options_chain import get_spot_with_fallback
        spot, _ = get_spot_with_fallback("TSLA")
        if spot <= 0:
            return 0.0
        rows = cache.get_chain(expiry)
        # Find ATM call (closest strike to spot)
        calls = [r for r in rows if r.option_type == "CALL" and r.implied_volatility > 0]
        if not calls:
            return 0.0
        atm = min(calls, key=lambda r: abs(r.strike - spot))
        return atm.implied_volatility
    except Exception as exc:
        logger.debug("_atm_iv failed: %s", exc)
        return 0.0


def get_chop_regime(force_refresh: bool = False) -> dict:
    """
    Compute the current market-chop regime for TSLA.

    Returns a dict with regime, score, components, thresholds_hit, ts, source.
    Cached per _chop_ttl().  Returns a TRENDING fallback on any fetch failure.
    """
    global _chop_cache, _chop_cache_ts
    now = time.time()
    ttl = _chop_ttl()
    if not force_refresh and _chop_cache is not None and now - _chop_cache_ts < ttl:
        return _chop_cache

    try:
        if yf is None:
            raise ImportError("yfinance not available")
        tsla = yf.Ticker("TSLA")
        hist = tsla.history(period="120d")

        if hist.empty or len(hist) < 30:
            logger.warning("ChopRegime: insufficient history (%d rows)", len(hist))
            return _trending_fallback("insufficient_history")

        opens  = hist["Open"].tolist()
        highs  = hist["High"].tolist()
        lows   = hist["Low"].tolist()
        closes = hist["Close"].tolist()

        # 1. Range ratio — 5d avg (H-L)/|C-O|; > 3.0 counts as choppy
        rr_val = _range_ratio(opens, highs, lows, closes, days=5)
        rr_chop = rr_val > 3.0

        # 2. ADX — 14-period; < 20 counts as choppy
        adx_val = _wilder_adx(highs, lows, closes, period=14)
        adx_chop = adx_val < 20.0

        # 3. BB squeeze — 20d/90d-median; < 0.6 counts as squeeze/chop
        bb_ratio = _bollinger_squeeze(closes, short_period=20, long_period=90)
        bb_chop = bb_ratio is not None and bb_ratio < 0.6

        # 4. RV/IV ratio — 5d realized < 0.7 × ATM 30d IV
        rv = _realized_vol_5d(closes)
        atm_iv = _atm_iv()
        if atm_iv > 0 and rv > 0:
            rv_iv_ratio = round(rv / atm_iv, 4)
        else:
            rv_iv_ratio = 1.0  # neutral if we can't compute
        rv_iv_chop = rv_iv_ratio < 0.7

        # Composite score (0.25 per component)
        score = sum([
            0.25 if rr_chop else 0.0,
            0.25 if adx_chop else 0.0,
            0.25 if bb_chop else 0.0,
            0.25 if rv_iv_chop else 0.0,
        ])

        thresholds_hit = []
        if rr_chop:
            thresholds_hit.append("range_ratio")
        if adx_chop:
            thresholds_hit.append("adx")
        if bb_chop:
            thresholds_hit.append("bb_squeeze")
        if rv_iv_chop:
            thresholds_hit.append("rv_iv_ratio")

        if score >= 0.75:
            regime = "CHOPPY"
        elif score >= 0.50:
            regime = "MIXED"
        else:
            regime = "TRENDING"

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        result = {
            "regime": regime,
            "score": round(score, 4),
            "components": {
                "range_ratio": rr_val,
                "adx": adx_val,
                "bb_squeeze": round(bb_ratio, 4) if bb_ratio is not None else None,
                "rv_iv_ratio": rv_iv_ratio,
            },
            "thresholds_hit": thresholds_hit,
            "ts": ts,
            "source": "yfinance + computed",
            "rv": rv,
            "atm_iv": atm_iv,
        }

        _chop_cache = result
        _chop_cache_ts = now
        _hb("chop_regime", status="ok", detail=f"regime:{regime} score:{score:.2f}")
        logger.info("ChopRegime: %s score=%.2f components=%s", regime, score, thresholds_hit)
        return result

    except Exception as exc:
        logger.warning("ChopRegime computation failed: %s", exc)
        _hb("chop_regime", status="error", detail=str(exc))
        return _trending_fallback(str(exc))


def _trending_fallback(reason: str) -> dict:
    """Safe fallback when chop regime cannot be computed."""
    return {
        "regime": "TRENDING",
        "score": 0.0,
        "components": {
            "range_ratio": None,
            "adx": None,
            "bb_squeeze": None,
            "rv_iv_ratio": None,
        },
        "thresholds_hit": [],
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "fallback",
        "error": reason,
    }


if __name__ == "__main__":
    import json, logging as _log
    _log.basicConfig(level=_log.INFO)
    result = get_chop_regime(force_refresh=True)
    print(json.dumps(result, indent=2))

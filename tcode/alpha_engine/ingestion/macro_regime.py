#!/usr/bin/env python3
"""
Macro Regime Detection: Fed rate, yield curve, VIX term structure, trade tensions.
Classifies the macro environment as RISK_ON, RISK_OFF, or NEUTRAL.
"""
import time
import logging
from typing import Optional

logger = logging.getLogger("MacroRegime")

_macro_cache: Optional[dict] = None
_macro_cache_ts: float = 0.0
_MACRO_TTL = 3600  # 1 hour for macro data
_VIX_TTL = 300     # 5 minutes for VIX term structure

_vix_cache: Optional[dict] = None
_vix_cache_ts: float = 0.0


def _fetch_macro_data() -> dict:
    """Fetch macro indicators from yfinance (yield curve, SPY trend, DXY, China ETF)."""
    try:
        import yfinance as yf

        result = {
            "yield_curve_inverted": False,
            "dxy_trend": "NEUTRAL",
            "china_risk": 0.0,
            "spy_trend": "NEUTRAL",
            "treasury_10y": 0.0,
            "treasury_2y": 0.0,
        }

        # 10Y Treasury yield
        try:
            tnx = yf.Ticker("^TNX")
            hist = tnx.history(period="5d")
            if not hist.empty:
                result["treasury_10y"] = round(float(hist["Close"].iloc[-1]), 2)
        except Exception:
            pass

        # 2Y Treasury yield (proxy via ^IRX or estimate)
        try:
            irx = yf.Ticker("^IRX")  # 13-week T-bill rate
            hist = irx.history(period="5d")
            if not hist.empty:
                result["treasury_2y"] = round(float(hist["Close"].iloc[-1]), 2)
                # Yield curve: 10Y - 2Y spread (using 13-week as proxy)
                spread = result["treasury_10y"] - result["treasury_2y"]
                result["yield_curve_inverted"] = spread < 0
        except Exception:
            pass

        # SPY 20-day trend
        try:
            spy = yf.Ticker("SPY")
            hist = spy.history(period="1mo")
            if len(hist) >= 10:
                recent = float(hist["Close"].iloc[-1])
                past = float(hist["Close"].iloc[-10])
                change = ((recent - past) / past) * 100
                result["spy_trend"] = "BULLISH" if change > 3 else "BEARISH" if change < -3 else "NEUTRAL"
        except Exception:
            pass

        # China ETF (FXI) as trade tension proxy
        try:
            fxi = yf.Ticker("FXI")
            hist = fxi.history(period="5d")
            if len(hist) >= 2:
                current = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2])
                fxi_change = ((current - prev) / prev) * 100
                # Large FXI drop = trade tension risk
                result["china_risk"] = round(max(0, -fxi_change * 2), 1)  # 0-10 scale
        except Exception:
            pass

        return result
    except Exception as e:
        logger.warning(f"Macro data fetch failed: {e}")
        return {"yield_curve_inverted": False, "spy_trend": "NEUTRAL", "china_risk": 0.0}


def _fetch_vix_term_structure() -> dict:
    """Fetch VIX term structure: spot VIX vs 9-day VIX."""
    try:
        import yfinance as yf

        result = {
            "vix_spot": 0.0,
            "vix_9d": 0.0,
            "term_structure": "CONTANGO",  # Normal: VIX9D < VIX (market calm)
        }

        try:
            vix = yf.Ticker("^VIX")
            hist = vix.history(period="2d")
            if not hist.empty:
                result["vix_spot"] = round(float(hist["Close"].iloc[-1]), 2)
        except Exception:
            pass

        try:
            vix9d = yf.Ticker("^VIX9D")
            hist = vix9d.history(period="2d")
            if not hist.empty:
                result["vix_9d"] = round(float(hist["Close"].iloc[-1]), 2)
        except Exception:
            pass

        if result["vix_spot"] > 0 and result["vix_9d"] > 0:
            if result["vix_9d"] > result["vix_spot"]:
                result["term_structure"] = "BACKWARDATION"  # Fear: near-term vol > long-term
            else:
                result["term_structure"] = "CONTANGO"  # Normal

        return result
    except Exception as e:
        logger.warning(f"VIX term structure fetch failed: {e}")
        return {"vix_spot": 0.0, "vix_9d": 0.0, "term_structure": "CONTANGO"}


def get_macro_regime() -> dict:
    """Return macro regime classification. Macro data cached 1hr, VIX 5min."""
    global _macro_cache, _macro_cache_ts, _vix_cache, _vix_cache_ts
    now = time.time()

    if _macro_cache is None or now - _macro_cache_ts > _MACRO_TTL:
        _macro_cache = _fetch_macro_data()
        _macro_cache_ts = now

    if _vix_cache is None or now - _vix_cache_ts > _VIX_TTL:
        _vix_cache = _fetch_vix_term_structure()
        _vix_cache_ts = now

    # Classify regime
    macro = {**_macro_cache, **_vix_cache}

    risk_off_signals = 0
    risk_on_signals = 0

    if macro.get("yield_curve_inverted"):
        risk_off_signals += 2  # Strong recession signal
    if macro.get("vix_spot", 0) > 30:
        risk_off_signals += 2
    elif macro.get("vix_spot", 0) > 25:
        risk_off_signals += 1
    if macro.get("term_structure") == "BACKWARDATION":
        risk_off_signals += 1  # Near-term fear
    if macro.get("china_risk", 0) > 5:
        risk_off_signals += 1
    if macro.get("spy_trend") == "BEARISH":
        risk_off_signals += 1

    if macro.get("spy_trend") == "BULLISH":
        risk_on_signals += 2
    if macro.get("vix_spot", 0) < 15:
        risk_on_signals += 1
    if macro.get("term_structure") == "CONTANGO":
        risk_on_signals += 1

    if risk_off_signals >= 3:
        macro["regime"] = "RISK_OFF"
    elif risk_on_signals >= 3:
        macro["regime"] = "RISK_ON"
    else:
        macro["regime"] = "NEUTRAL"

    return macro


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    result = get_macro_regime()
    print(json.dumps(result, indent=2))

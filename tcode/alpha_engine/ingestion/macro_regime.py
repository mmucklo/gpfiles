#!/usr/bin/env python3
"""
Macro Regime Detection: Fed rate, yield curve, VIX term structure, trade tensions.
Classifies the macro environment as RISK_ON, RISK_OFF, or NEUTRAL.

DXY sourcing (Phase 13.5):
  ^DXY was delisted from yfinance. We now try:
    1. DX-Y.NYB  — ICE US Dollar Index futures continuous contract (primary)
    2. UUP       — Invesco DB US Dollar Index Bullish ETF (proxy; ~1:1 directional)
  If both fail, dxy_status="unavailable" and dxy_trend="NEUTRAL" (no stale zeros).
  Cache: 5 min during US hours, 15 min off-hours.
"""
import time
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("MacroRegime")

try:
    import sys as _sys
    _sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")
    from heartbeat import emit_heartbeat as _hb
except Exception:
    def _hb(component, status="ok", detail=None, **_kw): pass  # type: ignore

try:
    from pause_guard import pause_guard as _pause_guard
except ImportError:  # pragma: no cover
    def _pause_guard(fn):  # type: ignore[misc]
        return fn

_macro_cache: Optional[dict] = None
_macro_cache_ts: float = 0.0
_MACRO_TTL = 3600  # 1 hour for macro data
_VIX_TTL = 300     # 5 minutes for VIX term structure

_vix_cache: Optional[dict] = None
_vix_cache_ts: float = 0.0

# DXY cache — separate because it has a different TTL than the macro block
_dxy_cache: Optional[dict] = None
_dxy_cache_ts: float = 0.0


def _dxy_ttl() -> float:
    """5 min during US market hours (9:30–16:00 ET), 15 min otherwise."""
    et_hour = datetime.now(timezone.utc).hour - 4  # rough EDT offset
    if 9 <= et_hour < 16:
        return 300
    return 900


def _fetch_dxy() -> dict:
    """
    Fetch US Dollar Index.

    Tries DX-Y.NYB (ICE futures) first. Falls back to UUP (ETF proxy) if that
    fails or returns empty. Returns status field so callers can show a source badge.

    Never returns a stale value beyond the TTL — callers are responsible for
    checking _dxy_cache_ts.
    """
    global _dxy_cache, _dxy_cache_ts
    now = time.time()
    if _dxy_cache is not None and now - _dxy_cache_ts < _dxy_ttl():
        return _dxy_cache

    try:
        import yfinance as yf

        # Primary: ICE US Dollar Index futures
        try:
            hist = yf.Ticker("DX-Y.NYB").history(period="2d")
            if not hist.empty and len(hist) >= 1:
                price = float(hist["Close"].iloc[-1])
                chg_pct = None
                if len(hist) >= 2:
                    prev = float(hist["Close"].iloc[-2])
                    chg_pct = round((price - prev) / prev * 100, 3) if prev else None
                result = {
                    "dxy": round(price, 3),
                    "dxy_change_pct": chg_pct,
                    "dxy_status": "live",
                    "dxy_source": "DX-Y.NYB",
                }
                _dxy_cache = result
                _dxy_cache_ts = now
                return result
        except Exception as e:
            logger.debug("DX-Y.NYB fetch failed: %s", e)

        # Fallback: UUP ETF proxy
        try:
            hist = yf.Ticker("UUP").history(period="2d")
            if not hist.empty and len(hist) >= 1:
                price = float(hist["Close"].iloc[-1])
                chg_pct = None
                if len(hist) >= 2:
                    prev = float(hist["Close"].iloc[-2])
                    chg_pct = round((price - prev) / prev * 100, 3) if prev else None
                result = {
                    "dxy": round(price, 3),
                    "dxy_change_pct": chg_pct,
                    "dxy_status": "uup_proxy",
                    "dxy_source": "UUP",
                }
                _dxy_cache = result
                _dxy_cache_ts = now
                return result
        except Exception as e:
            logger.debug("UUP fallback fetch failed: %s", e)

    except ImportError:
        pass

    logger.warning("[DXY-UNAVAILABLE] Both DX-Y.NYB and UUP failed at %s", time.strftime("%Y-%m-%dT%H:%M:%SZ"))
    result = {
        "dxy": None,
        "dxy_change_pct": None,
        "dxy_status": "unavailable",
        "dxy_source": None,
    }
    _dxy_cache = result
    _dxy_cache_ts = now
    return result


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

        # DXY: use resilient fetcher (DX-Y.NYB primary, UUP proxy fallback)
        dxy_data = _fetch_dxy()
        if dxy_data["dxy_status"] != "unavailable" and dxy_data.get("dxy_change_pct") is not None:
            chg = dxy_data["dxy_change_pct"]
            result["dxy_trend"] = "RISING" if chg > 0.2 else "FALLING" if chg < -0.2 else "NEUTRAL"
        result["dxy"] = dxy_data.get("dxy")
        result["dxy_change_pct"] = dxy_data.get("dxy_change_pct")
        result["dxy_status"] = dxy_data.get("dxy_status", "unavailable")
        result["dxy_source"] = dxy_data.get("dxy_source")

        return result
    except Exception as e:
        logger.warning(f"Macro data fetch failed: {e}")
        return {"yield_curve_inverted": False, "spy_trend": "NEUTRAL", "china_risk": 0.0}


def _fetch_tsla_realized_vol(window: int = 20) -> float:
    """
    Compute TSLA 20-day annualized realized volatility from daily log-returns.

    Realized vol = std(log-returns) * sqrt(252).  Used in vol-targeting Kelly to
    compare against implied vol (ATM IV from options chain).

    If IV > realized, options are "rich" → shrink position (vol ratio < 1).
    If IV < realized, options are "cheap" relative to realized → hold at full Kelly.
    """
    try:
        import yfinance as yf
        import math
        hist = yf.Ticker("TSLA").history(period="1mo")
        closes = [float(c) for c in hist["Close"].tolist() if c > 0]
        if len(closes) < window + 1:
            return 0.0
        # Use the most recent `window` return observations
        closes = closes[-(window + 1):]
        log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        if len(log_returns) < 2:
            return 0.0
        mean = sum(log_returns) / len(log_returns)
        variance = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
        realized_vol = (variance ** 0.5) * (252 ** 0.5)
        return round(realized_vol, 4)
    except Exception as e:
        logger.debug(f"TSLA realized vol fetch failed: {e}")
        return 0.0


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


@_pause_guard
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

    # Realized vol (20-day annualized) — used by publisher.py vol-targeting Kelly
    # Cached at VIX frequency (5 min) since it's similarly latency-tolerant
    macro["tsla_realized_vol"] = _fetch_tsla_realized_vol()

    _hb("macro_regime", status="ok", detail=f"regime:{macro.get('regime','NEUTRAL')} vix:{macro.get('vix_spot',0):.1f}")
    return macro


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    result = get_macro_regime()
    print(json.dumps(result, indent=2))

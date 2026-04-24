"""
TSLA Alpha Engine: Multi-Source Intelligence Aggregator
Fetches news sentiment, VIX/macro context, options flow, and earnings calendar.
All sources use yfinance (free, no auth required).
Cache TTL: 5 minutes.

CLI:
  python3 alpha_engine/ingestion/intel.py
"""
import json
import time
from typing import Any

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

# Module-level cache: {"data": ..., "ts": float}
_cache: dict[str, Any] = {}
_CACHE_TTL = 300  # 5 minutes

_BULLISH_KEYWORDS = {
    "surge", "rally", "beat", "upgrade", "bull", "buy", "breakout",
    "positive", "growth", "record", "soar", "jump",
}
_BEARISH_KEYWORDS = {
    "crash", "drop", "sell", "downgrade", "bear", "miss", "decline",
    "negative", "loss", "plunge", "fall", "risk", "warning",
}


def _vix_status(level: float) -> str:
    if level < 15:
        return "LOW"
    if level < 25:
        return "NORMAL"
    if level < 35:
        return "HIGH"
    return "EXTREME"


def _fetch_news_sentiment() -> dict:
    try:
        import yfinance as yf
        tsla = yf.Ticker("TSLA")
        news = tsla.news or []
        headlines = []
        bull_score = 0
        bear_score = 0
        for item in news[:20]:
            title = (
                item.get("title")
                or (item.get("content", {}) or {}).get("title", "")
            )
            if not title:
                continue
            words = title.lower().split()
            word_set = set(words)
            b = len(word_set & _BULLISH_KEYWORDS)
            be = len(word_set & _BEARISH_KEYWORDS)
            bull_score += b
            bear_score += be
            headlines.append(title)
        headlines = headlines[:5]
        total = bull_score + bear_score
        if total == 0:
            sentiment_score = 0.0
        else:
            sentiment_score = round((bull_score - bear_score) / total, 4)
        return {
            "headlines": headlines,
            "sentiment_score": sentiment_score,
            "headline_count": len(headlines),
            "bull_hits": bull_score,
            "bear_hits": bear_score,
        }
    except Exception as e:
        return {
            "headlines": [],
            "sentiment_score": 0.0,
            "headline_count": 0,
            "bull_hits": 0,
            "bear_hits": 0,
            "error": str(e),
        }


def _fetch_vix() -> dict:
    try:
        import yfinance as yf
        vix = yf.Ticker("^VIX")
        hist = vix.history(period="1d")
        if hist.empty:
            raise ValueError("empty VIX history")
        level = float(hist["Close"].iloc[-1])
        return {"vix_level": round(level, 2), "vix_status": _vix_status(level)}
    except Exception as e:
        return {"vix_level": None, "vix_status": "NORMAL", "error": str(e)}


def _fetch_spy() -> dict:
    try:
        import yfinance as yf
        spy = yf.Ticker("SPY")
        hist = spy.history(period="2d")
        if len(hist) < 2:
            raise ValueError("insufficient SPY history")
        prev_close = float(hist["Close"].iloc[-2])
        curr_close = float(hist["Close"].iloc[-1])
        change_pct = round((curr_close - prev_close) / prev_close * 100, 3)
        return {"spy_price": round(curr_close, 2), "spy_change_pct": change_pct}
    except Exception as e:
        return {"spy_price": None, "spy_change_pct": 0.0, "error": str(e)}


def _fetch_earnings() -> dict:
    try:
        import yfinance as yf
        from datetime import date
        tsla = yf.Ticker("TSLA")
        cal = tsla.calendar
        # calendar can be a DataFrame or dict depending on yf version
        next_date = None
        if cal is not None:
            if hasattr(cal, "columns"):
                # DataFrame: row index may be "Earnings Date"
                if "Earnings Date" in cal.index:
                    val = cal.loc["Earnings Date"]
                    if hasattr(val, "iloc"):
                        val = val.iloc[0]
                    import pandas as pd
                    if pd.notna(val):
                        next_date = str(val)[:10]
            elif isinstance(cal, dict):
                raw = cal.get("Earnings Date") or cal.get("earnings_date")
                if raw is not None:
                    if isinstance(raw, (list, tuple)):
                        raw = raw[0]
                    next_date = str(raw)[:10]
        days_until = None
        if next_date:
            try:
                nd = date.fromisoformat(next_date)
                days_until = (nd - date.today()).days
            except ValueError:
                pass
        return {"next_earnings_date": next_date, "days_until_earnings": days_until}
    except Exception as e:
        return {"next_earnings_date": None, "days_until_earnings": None, "error": str(e)}


def _fetch_options_flow() -> dict:
    try:
        import yfinance as yf
        tsla = yf.Ticker("TSLA")
        exps = tsla.options
        if not exps:
            raise ValueError("no expirations available")
        chain = tsla.option_chain(exps[0])
        call_oi = int(chain.calls["openInterest"].sum())
        put_oi = int(chain.puts["openInterest"].sum())
        if call_oi == 0:
            raise ValueError("zero call OI")
        pc_ratio = round(put_oi / call_oi, 4)
        if pc_ratio > 1.2:
            pc_signal = "BEARISH"
        elif pc_ratio < 0.7:
            pc_signal = "BULLISH"
        else:
            pc_signal = "NEUTRAL"
        return {
            "pc_ratio": pc_ratio,
            "pc_signal": pc_signal,
            "total_call_oi": call_oi,
            "total_put_oi": put_oi,
        }
    except Exception as e:
        return {
            "pc_ratio": 1.0,
            "pc_signal": "NEUTRAL",
            "total_call_oi": 0,
            "total_put_oi": 0,
            "error": str(e),
        }


@_pause_guard
def get_intel() -> dict:
    """
    Fetch all intel sources. Results are cached for 5 minutes.
    Returns a dict with news, vix, spy, earnings, and options_flow sections.
    """
    global _cache
    now = time.time()
    if _cache.get("ts") and now - _cache["ts"] < _CACHE_TTL:
        return _cache["data"]

    news = _fetch_news_sentiment()
    vix = _fetch_vix()
    spy = _fetch_spy()
    earnings = _fetch_earnings()
    options_flow = _fetch_options_flow()

    # Catalyst tracker (Musk + analysts)
    try:
        from ingestion.catalyst_tracker import get_catalyst_intel
        catalyst = get_catalyst_intel()
    except Exception as e:
        catalyst = {"musk_sentiment": 0.0, "analyst_consensus": "N/A"}

    # Institutional flow (13F + insider)
    try:
        from ingestion.institutional import get_institutional_intel
        institutional = get_institutional_intel()
    except Exception as e:
        institutional = {"net_insider_sentiment": "NEUTRAL", "top_holders": []}

    # EV sector signals
    try:
        from ingestion.ev_sector import get_ev_sector_intel
        ev_sector = get_ev_sector_intel()
    except Exception as e:
        ev_sector = {"sector_direction": "NEUTRAL", "tsla_relative_strength": 0.0}

    # Macro regime
    try:
        from ingestion.macro_regime import get_macro_regime
        macro_regime = get_macro_regime()
    except Exception as e:
        macro_regime = {"regime": "NEUTRAL"}

    # Pre-market intelligence
    try:
        from ingestion.premarket import get_premarket_intel
        premarket = get_premarket_intel()
    except Exception as e:
        premarket = {"is_premarket": False, "futures_bias": "FLAT", "composite_bias": "FLAT"}

    # Congressional STOCK Act disclosure lag-arb
    try:
        from ingestion.congress_trades import get_congress_trades
        congress = get_congress_trades()
    except Exception as e:
        congress = {
            "signal": "NEUTRAL",
            "sentiment_multiplier": 1.0,
            "committee_weighted_buy_48h": False,
            "committee_weighted_sell_48h": False,
            "recent_count": 0,
            "filing_count": 0,
        }

    # TSLA↔Mag7 correlation regime (IDIOSYNCRATIC / MACRO_LOCKED / NORMAL)
    try:
        from ingestion.correlation_regime import get_correlation_regime
        correlation_regime = get_correlation_regime()
    except Exception as e:
        correlation_regime = {"regime": "NORMAL", "error": str(e)}

    # Phase 14: market-chop regime (TRENDING / MIXED / CHOPPY)
    try:
        from ingestion.chop_regime import get_chop_regime
        chop_regime = get_chop_regime()
    except Exception as e:
        chop_regime = {
            "regime": "TRENDING",
            "score": 0.0,
            "components": {"range_ratio": None, "adx": None, "bb_squeeze": None, "rv_iv_ratio": None},
            "thresholds_hit": [],
            "ts": None,
            "source": "fallback",
            "error": str(e),
        }

    result = {
        "fetch_timestamp": now,
        "news": news,
        "vix": vix,
        "spy": spy,
        "earnings": earnings,
        "options_flow": options_flow,
        "catalyst": catalyst,
        "institutional": institutional,
        "ev_sector": ev_sector,
        "macro_regime": macro_regime,
        "premarket": premarket,
        "congress": congress,
        "correlation_regime": correlation_regime,
        "chop_regime": chop_regime,
    }
    # Sanitize NaN/Inf values (yfinance returns NaN for missing data)
    import math
    def _sanitize(obj):
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return 0.0
        if isinstance(obj, dict):
            return {k: _sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_sanitize(v) for v in obj]
        return obj
    result = _sanitize(result)

    _cache = {"data": result, "ts": now}
    _hb("intel_refresh", status="ok", detail=f"sources:news,vix,spy,earnings,options_flow,catalyst,institutional,ev_sector,macro_regime,premarket,congress,correlation_regime,chop_regime")
    return result


if __name__ == "__main__":
    print(json.dumps(get_intel(), indent=2))

#!/usr/bin/env python3
"""
Pre-Market Intelligence: ES/NQ futures, European indices, TSLA pre-market volume.
Generates PREMARKET signals between 7:00-9:30 AM ET.
"""
import time
import logging
from typing import Optional
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("PreMarket")

_premarket_cache: Optional[dict] = None
_premarket_cache_ts: float = 0.0
_PREMARKET_TTL = 60  # 1 minute — needs freshness


def _is_premarket_hours() -> bool:
    """Check if current time is in pre-market window (4:00-9:30 AM ET)."""
    et = datetime.now(timezone(timedelta(hours=-4)))  # EDT
    t = et.hour * 60 + et.minute
    return 240 <= t < 570  # 4:00 AM - 9:30 AM


def _is_signal_window() -> bool:
    """Check if current time is in signal generation window (7:00-9:30 AM ET)."""
    et = datetime.now(timezone(timedelta(hours=-4)))
    t = et.hour * 60 + et.minute
    return 420 <= t < 570  # 7:00 AM - 9:30 AM


def _fetch_premarket() -> dict:
    """Fetch futures, European indices, and TSLA pre-market data."""
    try:
        import yfinance as yf

        result = {
            "is_premarket": _is_premarket_hours(),
            "is_signal_window": _is_signal_window(),
            "futures_bias": "FLAT",
            "es_change_pct": 0.0,
            "nq_change_pct": 0.0,
            "europe_direction": "CLOSED",
            "tsla_premarket_change_pct": 0.0,
            "tsla_premarket_volume": 0,
            "overnight_catalyst": None,
        }

        # ES futures (S&P 500)
        try:
            es = yf.Ticker("ES=F")
            hist = es.history(period="2d")
            if len(hist) >= 2:
                current = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2])
                result["es_change_pct"] = round(((current - prev) / prev) * 100, 2)
        except Exception as e:
            logger.debug(f"ES futures failed: {e}")

        # NQ futures (Nasdaq 100)
        try:
            nq = yf.Ticker("NQ=F")
            hist = nq.history(period="2d")
            if len(hist) >= 2:
                current = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2])
                result["nq_change_pct"] = round(((current - prev) / prev) * 100, 2)
        except Exception as e:
            logger.debug(f"NQ futures failed: {e}")

        # Futures bias
        avg_futures = (result["es_change_pct"] + result["nq_change_pct"]) / 2
        if avg_futures > 0.5:
            result["futures_bias"] = "BULLISH"
        elif avg_futures < -0.5:
            result["futures_bias"] = "BEARISH"

        # European indices
        try:
            stoxx = yf.Ticker("^STOXX50E")
            hist = stoxx.history(period="2d")
            if len(hist) >= 2:
                current = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2])
                eu_change = ((current - prev) / prev) * 100
                result["europe_direction"] = "BULLISH" if eu_change > 0.5 else "BEARISH" if eu_change < -0.5 else "FLAT"
        except Exception:
            pass

        # TSLA pre-market (with prepost=True)
        try:
            tsla = yf.Ticker("TSLA")
            hist = tsla.history(period="1d", prepost=True)
            if not hist.empty:
                result["tsla_premarket_volume"] = int(hist["Volume"].sum())
                if len(hist) >= 2:
                    current = float(hist["Close"].iloc[-1])
                    prev = float(hist["Close"].iloc[0])
                    if prev > 0:
                        result["tsla_premarket_change_pct"] = round(((current - prev) / prev) * 100, 2)
        except Exception:
            pass

        return result
    except Exception as e:
        logger.warning(f"Pre-market fetch failed: {e}")
        return {"is_premarket": False, "futures_bias": "FLAT", "is_signal_window": False}


def get_premarket_intel() -> dict:
    """Return pre-market intel. Cached 1 minute."""
    global _premarket_cache, _premarket_cache_ts
    now = time.time()

    if _premarket_cache is None or now - _premarket_cache_ts > _PREMARKET_TTL:
        _premarket_cache = _fetch_premarket()
        _premarket_cache_ts = now

    return _premarket_cache


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    result = get_premarket_intel()
    print(json.dumps(result, indent=2))

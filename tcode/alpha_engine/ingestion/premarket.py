#!/usr/bin/env python3
"""
Pre-Market Intelligence: US futures, international indices (Europe + Asia), FX, TSLA pre-post.

Composite bias weights (grounded in market-open sequencing):
  Asia   30% — closes 12-14h before US open; leading indicator for overnight risk
  Europe 40% — concurrent mid-session; highest correlation to US gap at open
  US fut 30% — ES/NQ futures are the most directly predictive of the open

FX override: DXY or USDJPY move >0.5% adds ±0.20 to confidence (carry/dollar signals).

Signal window gate: PREMARKET signals only emitted 7:00–9:30 AM ET.
Before 7:00 AM, data is returned but no signal is generated.
"""
import time
import logging
from typing import Optional
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("PreMarket")

_premarket_cache: Optional[dict] = None
_premarket_cache_ts: float = 0.0
_PREMARKET_TTL = 60  # 1 minute — needs freshness during pre-market window


def _is_premarket_hours() -> bool:
    """Check if current time is in pre-market window (4:00–9:30 AM ET)."""
    et = datetime.now(timezone(timedelta(hours=-4)))  # EDT
    t = et.hour * 60 + et.minute
    return 240 <= t < 570  # 4:00 AM – 9:30 AM


def _is_signal_window() -> bool:
    """Check if current time is in signal generation window (7:00–9:30 AM ET)."""
    et = datetime.now(timezone(timedelta(hours=-4)))
    t = et.hour * 60 + et.minute
    return 420 <= t < 570  # 7:00 AM – 9:30 AM


def _pct_change(hist) -> Optional[float]:
    """Compute day-over-day % change from a yfinance history DataFrame."""
    if hist is None or len(hist) < 2:
        return None
    current = float(hist["Close"].iloc[-1])
    prev = float(hist["Close"].iloc[-2])
    if prev == 0:
        return None
    return round((current - prev) / prev * 100, 2)


def _fetch_index(yf, symbol: str) -> dict:
    """Fetch a single ticker's last close and day-over-day change."""
    try:
        hist = yf.Ticker(symbol).history(period="2d")
        chg = _pct_change(hist)
        if chg is None:
            return {"symbol": symbol, "change_pct": 0.0, "ok": False}
        return {"symbol": symbol, "change_pct": chg, "ok": True}
    except Exception as e:
        logger.debug(f"Fetch failed for {symbol}: {e}")
        return {"symbol": symbol, "change_pct": 0.0, "ok": False}


def _bias_label(score: float) -> str:
    """Convert a scalar score to a bias label."""
    if score > 0.4:
        return "BULLISH"
    if score < -0.4:
        return "BEARISH"
    if abs(score) < 0.15:
        return "FLAT"
    return "MIXED"


def _fetch_premarket() -> dict:
    """
    Fetch US futures, European/Asian indices, FX, and TSLA pre-market data.

    Returns a structured dict with nested region keys plus a top-level
    composite_bias / confidence / rationale for downstream signal generation.
    Legacy flat fields (futures_bias, es_change_pct, etc.) are preserved for
    backward compatibility with existing publisher.py reads.
    """
    try:
        import yfinance as yf

        # ── Region data collection ─────────────────────────────────────────
        es   = _fetch_index(yf, "ES=F")
        nq   = _fetch_index(yf, "NQ=F")
        stoxx = _fetch_index(yf, "^STOXX50E")
        dax  = _fetch_index(yf, "^GDAXI")
        ftse = _fetch_index(yf, "^FTSE")
        n225 = _fetch_index(yf, "^N225")
        hsi  = _fetch_index(yf, "^HSI")
        sse  = _fetch_index(yf, "000001.SS")  # Shanghai Composite
        usdjpy = _fetch_index(yf, "USDJPY=X")
        eurusd = _fetch_index(yf, "EURUSD=X")
        dxy    = _fetch_index(yf, "^DXY")

        # ── TSLA pre/post-market ───────────────────────────────────────────
        tsla_premarket: dict = {"change_pct": 0.0, "volume": 0, "ok": False}
        try:
            tsla_hist = yf.Ticker("TSLA").history(period="1d", prepost=True)
            if not tsla_hist.empty:
                vol = int(tsla_hist["Volume"].sum())
                chg = 0.0
                if len(tsla_hist) >= 2:
                    cur = float(tsla_hist["Close"].iloc[-1])
                    prv = float(tsla_hist["Close"].iloc[0])
                    if prv > 0:
                        chg = round((cur - prv) / prv * 100, 2)
                tsla_premarket = {"change_pct": chg, "volume": vol, "ok": True}
        except Exception as e:
            logger.debug(f"TSLA pre-market fetch failed: {e}")

        # ── Composite bias scoring ─────────────────────────────────────────
        # Region scores are the mean % change of all valid tickers in that region.
        # A positive mean maps linearly to a bullish score in [-1, +1].
        # Threshold for counting as "directional": abs(chg) > 0.2%
        def region_score(members: list[dict]) -> float:
            valid = [m["change_pct"] for m in members if m["ok"]]
            if not valid:
                return 0.0
            avg = sum(valid) / len(valid)
            # Normalize: 1% move → ±0.5 score; 2%+ → ±1.0 (clamped)
            return max(-1.0, min(1.0, avg / 2.0))

        asia_score   = region_score([n225, hsi, sse])
        europe_score = region_score([stoxx, dax, ftse])
        us_score     = region_score([es, nq])

        # Weighted composite: Asia 30%, Europe 40%, US futures 30%
        composite_score = 0.30 * asia_score + 0.40 * europe_score + 0.30 * us_score

        # Base confidence from absolute magnitude of composite (0.5 floor)
        base_confidence = min(0.90, 0.5 + abs(composite_score) * 0.5)

        # FX override: risk-off DXY spike OR USDJPY carry unwind adjusts confidence
        # DXY up = dollar strength = risk-off → bearish equity bias
        # USDJPY down = yen strengthening = carry unwind = risk-off
        fx_adj = 0.0
        dxy_chg = dxy["change_pct"] if dxy["ok"] else 0.0
        usdjpy_chg = usdjpy["change_pct"] if usdjpy["ok"] else 0.0
        if abs(dxy_chg) > 0.5:
            fx_adj += 0.20
            # DXY direction is bearish for equities when rising
            if dxy_chg > 0.5 and composite_score > 0:
                composite_score -= 0.15  # dampen bullish bias
        if abs(usdjpy_chg) > 0.5:
            fx_adj += 0.20
        confidence = min(0.95, base_confidence + fx_adj)

        composite_bias = _bias_label(composite_score)

        # ── Human-readable rationale ───────────────────────────────────────
        rationale_parts = []
        if any(m["ok"] for m in [n225, hsi, sse]):
            asia_valid = [m for m in [n225, hsi, sse] if m["ok"]]
            asia_avg = sum(m["change_pct"] for m in asia_valid) / len(asia_valid)
            rationale_parts.append(f"Asia {asia_avg:+.1f}%")
        if any(m["ok"] for m in [stoxx, dax, ftse]):
            eu_valid = [m for m in [stoxx, dax, ftse] if m["ok"]]
            eu_avg = sum(m["change_pct"] for m in eu_valid) / len(eu_valid)
            rationale_parts.append(f"Europe {eu_avg:+.1f}%")
        if nq["ok"]:
            rationale_parts.append(f"NQ {nq['change_pct']:+.1f}%")
        if es["ok"]:
            rationale_parts.append(f"ES {es['change_pct']:+.1f}%")
        if dxy["ok"] and abs(dxy_chg) > 0.3:
            rationale_parts.append(f"DXY {dxy_chg:+.2f}%")
        if usdjpy["ok"] and abs(usdjpy_chg) > 0.3:
            rationale_parts.append(f"USDJPY {usdjpy_chg:+.2f}%")
        bias_str = composite_bias
        rationale = (", ".join(rationale_parts) + f" → {bias_str} bias") if rationale_parts else f"{bias_str} bias (insufficient data)"

        # Legacy flat fields preserved for backward compatibility
        avg_futures = (es["change_pct"] + nq["change_pct"]) / 2
        legacy_futures_bias = "BULLISH" if avg_futures > 0.5 else "BEARISH" if avg_futures < -0.5 else "FLAT"
        eu_change = stoxx["change_pct"]  # STOXX as legacy Europe proxy
        legacy_europe_direction = "BULLISH" if eu_change > 0.5 else "BEARISH" if eu_change < -0.5 else "FLAT"

        return {
            # ── Structured regional data ──
            "us_futures": {
                "ES": es,
                "NQ": nq,
            },
            "europe": {
                "STOXX50E": stoxx,
                "GDAXI": dax,
                "FTSE": ftse,
            },
            "asia": {
                "N225": n225,
                "HSI": hsi,
                "SSE": sse,
            },
            "fx": {
                "USDJPY": usdjpy,
                "EURUSD": eurusd,
                "DXY": dxy,
            },
            "tsla_premarket": tsla_premarket,
            # ── Top-level composite ──
            "composite_bias": composite_bias,
            "confidence": round(confidence, 3),
            "rationale": rationale,
            # ── Meta ──
            "is_premarket": _is_premarket_hours(),
            "is_signal_window": _is_signal_window(),
            # ── Legacy flat fields (publisher.py backward compat) ──
            "futures_bias": legacy_futures_bias,
            "es_change_pct": es["change_pct"],
            "nq_change_pct": nq["change_pct"],
            "europe_direction": legacy_europe_direction,
            "tsla_premarket_change_pct": tsla_premarket["change_pct"],
            "tsla_premarket_volume": tsla_premarket["volume"],
            "overnight_catalyst": None,
        }
    except Exception as e:
        logger.warning(f"Pre-market fetch failed: {e}")
        return {
            "is_premarket": False,
            "is_signal_window": False,
            "futures_bias": "FLAT",
            "composite_bias": "FLAT",
            "confidence": 0.0,
            "rationale": f"Data unavailable: {e}",
            "us_futures": {}, "europe": {}, "asia": {}, "fx": {},
            "tsla_premarket": {},
            "es_change_pct": 0.0,
            "nq_change_pct": 0.0,
        }


def get_premarket_intel() -> dict:
    """Return pre-market intel. Cached 1 minute for freshness during signal window."""
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

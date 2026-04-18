"""
Phase 17 — Real-time 1-minute TSLA bar feed.

Sources: Tradier /markets/timesales?symbol=TSLA&interval=1min
Poll cadence: 60 s (pause-aware).
Window: REALTIME_BAR_WINDOW bars (default 20).
Computed indicators per window:
  - ATR(14)              average true range over 14 bars
  - volume_ratio         current bar volume / 20-bar avg volume
  - vwap                 volume-weighted average price of window
  - bar_range_vs_atr     current bar range / ATR (wide = expansion)

Heartbeat component: "realtime_bars", expected cadence 60 s.

SQLite: bars persisted to price_bars table (source="tradier_1min").
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    pass

logger = logging.getLogger("RealtimeBars")

# ── Config ────────────────────────────────────────────────────────────────────
TRADIER_BASE_URL = os.getenv("TRADIER_BASE_URL", "https://api.tradier.com/v1")
TRADIER_API_TOKEN = os.getenv("TRADIER_API_TOKEN", "")
REALTIME_BAR_WINDOW: int = int(os.getenv("REALTIME_BAR_WINDOW", "20"))
ATR_PERIOD: int = int(os.getenv("ATR_PERIOD", "14"))
_POLL_INTERVAL_S: int = 60
DB_PATH = os.path.expanduser("~/tsla_alpha.db")

# ── In-memory rolling window ──────────────────────────────────────────────────
_bars: list[dict] = []          # newest at tail
_indicators: dict = {}          # latest computed indicators
_last_fetch_ts: float = 0.0


def _headers() -> dict:
    token = os.getenv("TRADIER_API_TOKEN", TRADIER_API_TOKEN)
    if not token:
        raise RuntimeError("TRADIER_API_TOKEN not set — cannot fetch realtime bars")
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


# ── Tradier fetch ─────────────────────────────────────────────────────────────

def _fetch_bars(start: datetime | None = None) -> list[dict]:
    """Fetch 1-min bars from Tradier timesales endpoint.

    Returns list of bar dicts: {ts, open, high, low, close, volume, vwap}.
    """
    if start is None:
        # Back-fill enough history for ATR window
        start = datetime.now(timezone.utc) - timedelta(minutes=REALTIME_BAR_WINDOW + 5)

    params = {
        "symbol": "TSLA",
        "interval": "1min",
        "start": start.strftime("%Y-%m-%d %H:%M"),
        "end": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "session_filter": "all",
    }
    base = os.getenv("TRADIER_BASE_URL", TRADIER_BASE_URL).rstrip("/")
    url = f"{base}/markets/timesales"

    try:
        resp = requests.get(url, headers=_headers(), params=params, timeout=10)
        if resp.status_code == 401:
            raise RuntimeError("Tradier 401 — check TRADIER_API_TOKEN")
        if resp.status_code == 429:
            logger.warning("Tradier rate-limited on timesales — skip this cycle")
            return []
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.warning("Tradier timesales request failed: %s", exc)
        return []

    series = (data.get("series") or {}).get("data") or []
    if isinstance(series, dict):
        series = [series]  # single-bar edge case

    bars = []
    for row in series:
        try:
            bars.append({
                "ts": row["time"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
                "vwap": float(row.get("vwap", row["close"])),
            })
        except (KeyError, ValueError, TypeError):
            continue
    return bars


# ── Indicators ────────────────────────────────────────────────────────────────

def _compute_atr(bars: list[dict], period: int) -> float:
    """True-range ATR over *period* bars. Returns 0.0 if insufficient data."""
    if len(bars) < 2:
        return 0.0
    trs = []
    for i in range(1, len(bars)):
        high = bars[i]["high"]
        low = bars[i]["low"]
        prev_close = bars[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if not trs:
        return 0.0
    tail = trs[-period:]  # use last `period` true ranges
    return sum(tail) / len(tail)


def _compute_indicators(bars: list[dict]) -> dict:
    """Compute ATR, volume ratio, VWAP, and bar_range_vs_atr from window."""
    if not bars:
        return {"atr": 0.0, "volume_ratio": 1.0, "vwap": 0.0, "bar_range_vs_atr": 0.0, "bar_count": 0}

    atr = _compute_atr(bars, ATR_PERIOD)
    avg_vol = sum(b["volume"] for b in bars) / len(bars) if bars else 1
    last = bars[-1]
    vol_ratio = last["volume"] / avg_vol if avg_vol > 0 else 1.0

    # VWAP of window
    total_pv = sum(b["vwap"] * b["volume"] for b in bars)
    total_v = sum(b["volume"] for b in bars)
    vwap = total_pv / total_v if total_v > 0 else (last["close"] if bars else 0.0)

    bar_range = last["high"] - last["low"]
    bar_range_vs_atr = bar_range / atr if atr > 0 else 0.0

    return {
        "atr": round(atr, 4),
        "volume_ratio": round(vol_ratio, 3),
        "vwap": round(vwap, 4),
        "bar_range_vs_atr": round(bar_range_vs_atr, 3),
        "bar_count": len(bars),
    }


# ── SQLite persistence ────────────────────────────────────────────────────────

def _persist_bar(bar: dict) -> None:
    """Write a single bar to price_bars (upsert by ts+ticker+source)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            INSERT OR REPLACE INTO price_bars (ts, ticker, source, open, high, low, close, volume)
            VALUES (?, 'TSLA', 'tradier_1min', ?, ?, ?, ?, ?)
        """, (bar["ts"], bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"]))
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.debug("Bar persist failed: %s", exc)


# ── Heartbeat ─────────────────────────────────────────────────────────────────

def _emit_heartbeat(status: str, detail: str = "") -> None:
    try:
        from heartbeat import emit_heartbeat
        emit_heartbeat("realtime_bars", status=status, detail=detail)
    except Exception:
        pass


# ── Public API ────────────────────────────────────────────────────────────────

def get_latest() -> dict:
    """Return latest bars + computed indicators (for /api/bars/latest)."""
    return {
        "bars": list(_bars),
        "indicators": dict(_indicators),
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def refresh(paused: bool = False, backfill: bool = False) -> None:
    """Fetch latest bars and update the rolling window.

    paused=True   → skip fetch (pause gate, Phase 16.1)
    backfill=True → fetch full window on unpause (start = now - window_size min)
    """
    global _bars, _indicators, _last_fetch_ts

    if paused:
        logger.debug("realtime_bars: paused — skipping fetch")
        return

    now = time.time()
    if not backfill and (now - _last_fetch_ts) < _POLL_INTERVAL_S - 2:
        return  # called too soon

    if backfill:
        start = datetime.now(timezone.utc) - timedelta(minutes=REALTIME_BAR_WINDOW + 5)
        logger.info("realtime_bars: backfilling %d bars from %s", REALTIME_BAR_WINDOW, start.isoformat())
        new_bars = _fetch_bars(start=start)
    else:
        # Just the last 2 bars (current + prev for true range)
        start = datetime.now(timezone.utc) - timedelta(minutes=3)
        new_bars = _fetch_bars(start=start)

    if not new_bars:
        _emit_heartbeat("degraded", "no bars returned from Tradier timesales")
        return

    # Merge into rolling window (deduplicate by ts)
    existing_ts = {b["ts"] for b in _bars}
    for b in new_bars:
        if b["ts"] not in existing_ts:
            _bars.append(b)
            existing_ts.add(b["ts"])
            _persist_bar(b)

    # Trim to window size
    _bars = sorted(_bars, key=lambda b: b["ts"])
    _bars = _bars[-REALTIME_BAR_WINDOW:]

    _indicators = _compute_indicators(_bars)
    _last_fetch_ts = now

    _emit_heartbeat("ok", f"atr={_indicators.get('atr', 0):.3f} vol_ratio={_indicators.get('volume_ratio', 0):.2f}")
    logger.debug("realtime_bars: %d bars loaded, ATR=%.4f", len(_bars), _indicators.get("atr", 0))


def get_atr() -> float:
    """Return latest ATR (0.0 if no data yet)."""
    return _indicators.get("atr", 0.0)


def get_latest_close() -> float:
    """Return the most recent bar's close price."""
    if not _bars:
        return 0.0
    return _bars[-1]["close"]


# ── Standalone poll loop (used when run directly) ─────────────────────────────

async def run_poll_loop() -> None:
    """Async poll loop — called from publisher or as standalone service."""
    import asyncio
    logger.info("realtime_bars: starting 60-second poll loop (window=%d, ATR_period=%d)",
                REALTIME_BAR_WINDOW, ATR_PERIOD)
    # Initial backfill
    refresh(paused=False, backfill=True)
    while True:
        await asyncio.sleep(_POLL_INTERVAL_S)
        try:
            from publisher import _read_pause_state
            state = _read_pause_state()
            is_paused = state.get("paused", True)
        except ImportError:
            is_paused = False
        refresh(paused=is_paused)

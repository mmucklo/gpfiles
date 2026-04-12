"""
TSLA Alpha Engine: TradingView Data Feed
Fetches real-time price bars from TradingView via tvDatafeed with a 60s TTL cache.
Credentials loaded from alpha_engine/.env — never hardcoded.

Cache TTL: 60s — matches options chain cache for consistency.
"""
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yfinance as yf
from dotenv import load_dotenv

logger = logging.getLogger("TVFeed")

# Load .env from alpha_engine directory (one level up from ingestion/)
_ENV_PATH = Path(__file__).parent.parent / ".env"
load_dotenv(_ENV_PATH)

try:
    from tvDatafeed import TvDatafeed, Interval as TvInterval
    _TV_AVAILABLE = True
except ImportError:
    _TV_AVAILABLE = False
    logger.warning("tvDatafeed not installed — TV feed disabled")


class TVFeedError(Exception):
    """Raised when TradingView data cannot be fetched."""
    pass


class TVFeedCache:
    """
    Thread-safe TradingView data cache with 60s TTL.
    Authenticates once on first use; re-auth on session expiry.
    """

    CACHE_TTL = 60      # seconds
    TV_EXCHANGE = "NASDAQ"

    def __init__(self):
        self._lock = threading.Lock()
        self._tv: Optional[object] = None  # TvDatafeed instance
        self._spot_cache: dict = {}         # symbol -> (ts, price)
        self._bars_cache: dict = {}         # symbol -> (ts, DataFrame)

    # ── auth ─────────────────────────────────────────────────────────────────

    def _get_client(self) -> object:
        """Return (or lazily create) the TvDatafeed client."""
        if not _TV_AVAILABLE:
            raise TVFeedError("tvDatafeed package not installed")
        if self._tv is None:
            username = os.getenv("TV_USERNAME", "")
            password = os.getenv("TV_PASSWORD", "")
            if not username or not password:
                raise TVFeedError(
                    "TV_USERNAME / TV_PASSWORD not set in .env — "
                    "cannot authenticate with TradingView"
                )
            try:
                self._tv = TvDatafeed(username, password)
                logger.info("TradingView session established")
            except Exception as exc:
                raise TVFeedError(f"TradingView login failed: {exc}") from exc
        return self._tv

    # ── public API ────────────────────────────────────────────────────────────

    def get_spot(self, symbol: str = "TSLA") -> float:
        """
        Return the most recent 1-minute close price for `symbol`.
        Raises TVFeedError on any failure.
        """
        now = time.time()
        with self._lock:
            cached = self._spot_cache.get(symbol)
            if cached and now - cached[0] < self.CACHE_TTL:
                logger.debug("TV spot cache hit: %s = %.2f", symbol, cached[1])
                return cached[1]

        # Fetch outside the lock to avoid blocking other threads
        tv = self._get_client()
        try:
            df = tv.get_hist(
                symbol,
                self.TV_EXCHANGE,
                interval=TvInterval.in_1_minute,
                n_bars=2,
            )
        except Exception as exc:
            raise TVFeedError(f"TradingView get_hist failed for {symbol}: {exc}") from exc

        if df is None or df.empty:
            raise TVFeedError(
                f"TradingView returned no data for {symbol} — "
                "market may be closed or symbol unavailable"
            )

        price = float(df["close"].iloc[-1])
        fetched_at = df.index[-1].isoformat()
        logger.info(
            "TV spot fetched: %s = %.2f (bar_time=%s, source=TradingView/1m)",
            symbol, price, fetched_at,
        )

        with self._lock:
            self._spot_cache[symbol] = (now, price)

        return price

    def get_daily_bars(self, symbol: str = "TSLA", n: int = 5):
        """
        Return a DataFrame of the last `n` daily OHLCV bars for `symbol`.
        Raises TVFeedError on any failure.
        """
        now = time.time()
        cache_key = f"{symbol}:{n}"
        with self._lock:
            cached = self._bars_cache.get(cache_key)
            if cached and now - cached[0] < self.CACHE_TTL:
                return cached[1]

        tv = self._get_client()
        try:
            df = tv.get_hist(
                symbol,
                self.TV_EXCHANGE,
                interval=TvInterval.in_daily,
                n_bars=n,
            )
        except Exception as exc:
            raise TVFeedError(f"TradingView daily bars failed for {symbol}: {exc}") from exc

        if df is None or df.empty:
            raise TVFeedError(f"TradingView returned no daily bars for {symbol}")

        logger.info(
            "TV daily bars fetched: %s x%d rows (source=TradingView/1D)",
            symbol, len(df),
        )

        with self._lock:
            self._bars_cache[cache_key] = (now, df)

        return df


# ── spot cross-validation ─────────────────────────────────────────────────────

def validate_spot_price(symbol: str = "TSLA") -> dict:
    """
    Cross-validate the TradingView spot price against yfinance.

    Returns:
        {
            "tv": float | None,
            "yf": float | None,
            "divergence_pct": float,
            "ok": bool,
            "warning": str | None,
            "timestamp": str,
        }
    """
    from datetime import datetime, timezone

    result: dict = {
        "tv": None,
        "yf": None,
        "divergence_pct": 0.0,
        "ok": False,
        "warning": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # --- TradingView ---
    try:
        result["tv"] = _cache.get_spot(symbol)
    except TVFeedError as exc:
        result["warning"] = f"TV feed error: {exc}"
        logger.warning("TV spot validation failed: %s", exc)
    except Exception as exc:
        result["warning"] = f"Unexpected TV error: {exc}"
        logger.exception("Unexpected error fetching TV spot")

    # --- yfinance ---
    try:
        ticker = yf.Ticker(symbol)
        yf_price = float(ticker.fast_info["lastPrice"])
        result["yf"] = yf_price
    except Exception:
        try:
            yf_price = float(ticker.history(period="1d")["Close"].iloc[-1])
            result["yf"] = yf_price
        except Exception as exc:
            if result["warning"] is None:
                result["warning"] = f"YF feed error: {exc}"
            logger.warning("YF spot validation failed: %s", exc)

    # --- cross-validate ---
    tv_price = result["tv"]
    yf_price = result["yf"]

    if tv_price is None or yf_price is None:
        result["ok"] = False
        if result["warning"] is None:
            result["warning"] = "One or both price sources unavailable"
        return result

    mid = (tv_price + yf_price) / 2.0
    if mid > 0:
        div_pct = abs(tv_price - yf_price) / mid * 100.0
    else:
        div_pct = 0.0
    result["divergence_pct"] = round(div_pct, 4)

    if div_pct >= 5.0:
        result["ok"] = False
        result["warning"] = (
            f"CRITICAL: {symbol} TV={tv_price:.2f} vs YF={yf_price:.2f} "
            f"diverge {div_pct:.2f}% — exceeds 5% threshold, skipping signal cycle"
        )
        logger.error("Spot divergence CRITICAL: %s", result["warning"])
    elif div_pct >= 2.0:
        result["ok"] = True  # still usable but flag it
        result["warning"] = (
            f"WARNING: {symbol} TV={tv_price:.2f} vs YF={yf_price:.2f} "
            f"diverge {div_pct:.2f}% — exceeds 2% threshold"
        )
        logger.warning("Spot divergence elevated: %s", result["warning"])
    else:
        result["ok"] = True
        logger.info(
            "Spot validation OK: %s TV=%.2f YF=%.2f div=%.3f%%",
            symbol, tv_price, yf_price, div_pct,
        )

    return result


# ── module-level singleton ─────────────────────────────────────────────────────

_cache: TVFeedCache = TVFeedCache()


def get_tv_cache() -> TVFeedCache:
    return _cache


# ── CLI self-test (also used as subprocess by api.go) ─────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    symbol = sys.argv[1] if len(sys.argv) > 1 else "TSLA"
    result = validate_spot_price(symbol)

    # Always output valid JSON to stdout for Go subprocess parsing
    print(json.dumps(result))

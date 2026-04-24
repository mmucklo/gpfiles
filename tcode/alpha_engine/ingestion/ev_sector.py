#!/usr/bin/env python3
"""
EV Sector Intelligence: competitor prices, sector ETF, correlation signals.
Adds EV_SECTOR ModelType for sector-wide move detection.
"""
import time
import logging
from typing import Optional

try:
    from pause_guard import pause_guard as _pause_guard
except ImportError:  # pragma: no cover
    def _pause_guard(fn):  # type: ignore[misc]
        return fn

logger = logging.getLogger("EVSector")

_ev_cache: Optional[dict] = None
_ev_cache_ts: float = 0.0
_EV_TTL = 300  # 5 minutes


def _fetch_ev_sector() -> dict:
    """Fetch EV competitor prices and sector ETF data from yfinance."""
    try:
        import yfinance as yf

        tickers = {
            "TSLA": "TSLA",
            "RIVN": "RIVN",
            "LCID": "LCID",
            "BYD": "1211.HK",
            "EV_ETF": "DRIV",
        }

        result = {
            "competitors": {},
            "sector_etf": {},
            "sector_direction": "NEUTRAL",
            "tsla_relative_strength": 0.0,
        }

        for label, symbol in tickers.items():
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="5d")
                if hist.empty or len(hist) < 2:
                    continue

                current = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2])
                change_pct = ((current - prev) / prev) * 100

                if label == "EV_ETF":
                    result["sector_etf"] = {
                        "price": round(current, 2),
                        "change_pct": round(change_pct, 2),
                        "symbol": symbol,
                    }
                else:
                    result["competitors"][label] = {
                        "price": round(current, 2),
                        "change_pct": round(change_pct, 2),
                    }
            except Exception as e:
                logger.debug(f"Failed to fetch {label} ({symbol}): {e}")

        # Compute sector direction
        competitor_changes = [v["change_pct"] for k, v in result["competitors"].items() if k != "TSLA"]
        if competitor_changes:
            avg_sector = sum(competitor_changes) / len(competitor_changes)
            tsla_change = result["competitors"].get("TSLA", {}).get("change_pct", 0)
            etf_change = result["sector_etf"].get("change_pct", 0)

            # Relative strength: TSLA vs sector average
            result["tsla_relative_strength"] = round(tsla_change - avg_sector, 2)

            # Sector direction
            if avg_sector > 2.0 and etf_change > 1.5:
                result["sector_direction"] = "BULLISH"
            elif avg_sector < -2.0 and etf_change < -1.5:
                result["sector_direction"] = "BEARISH"
            elif abs(avg_sector) < 0.5:
                result["sector_direction"] = "FLAT"

            # Detect divergence: TSLA flat but sector moving
            if abs(tsla_change) < 1.0 and abs(avg_sector) > 3.0:
                result["sector_direction"] = "DIVERGING"

        return result
    except Exception as e:
        logger.warning(f"EV sector fetch failed: {e}")
        return {"competitors": {}, "sector_etf": {}, "sector_direction": "NEUTRAL", "tsla_relative_strength": 0.0}


@_pause_guard
def get_ev_sector_intel() -> dict:
    """Return EV sector intel. Cached 5 minutes."""
    global _ev_cache, _ev_cache_ts
    now = time.time()

    if _ev_cache is None or now - _ev_cache_ts > _EV_TTL:
        _ev_cache = _fetch_ev_sector()
        _ev_cache_ts = now
        logger.info(f"EV sector refreshed: direction={_ev_cache.get('sector_direction')}, "
                     f"relative_strength={_ev_cache.get('tsla_relative_strength')}")

    return _ev_cache


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    result = get_ev_sector_intel()
    print(json.dumps(result, indent=2))

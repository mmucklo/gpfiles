"""
TSLA Alpha Engine: Data Source Audit Aggregator
Runs all data sources (IBKR, TV, yfinance) and returns a combined status dict.

Used by execution_engine/api.go as the /api/data/audit subprocess:
  python -m ingestion.audit TSLA
"""
import json
import sys
import time
import logging

logger = logging.getLogger("DataAudit")


def run_audit(symbol: str = "TSLA") -> dict:
    result = {
        "ibkr_connected": False,
        "ibkr_spot":      0.0,
        "primary_source": "yfinance",
        "tv":             None,
        "yf":             None,
        "divergence_pct": 0.0,
        "ok":             True,
        "warning":        None,
        "timestamp":      time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # ── 1. Try IBKR (primary) ─────────────────────────────────────────────
    try:
        from ingestion.ibkr_feed import IBKRFeed
        feed = IBKRFeed()
        if feed.connect():
            try:
                spot = feed.get_spot(symbol)
                result["ibkr_connected"] = True
                result["ibkr_spot"]      = spot["price"]
                result["primary_source"] = "ibkr"
                logger.info(f"IBKR spot {symbol}: ${spot['price']:.2f}")
            except Exception as e:
                result["ibkr_connected"] = feed.is_connected()
                logger.warning(f"IBKR spot failed after connect: {e}")
            finally:
                feed.disconnect()
        else:
            result["ibkr_connected"] = False
            logger.info("IBKR not available — using TV/YF fallback")
    except Exception as e:
        result["ibkr_connected"] = False
        logger.debug(f"IBKR import/connect error: {e}")

    # ── 2. Always run TV/YF cross-validation ─────────────────────────────
    try:
        from ingestion.tv_feed import validate_spot_price
        val = validate_spot_price(symbol)
        result["tv"]              = val.get("tv")
        result["yf"]              = val.get("yf")
        result["divergence_pct"]  = val.get("divergence_pct", 0.0)
        result["ok"]              = val.get("ok", True)
        result["warning"]         = val.get("warning")
        result["timestamp"]       = val.get("timestamp", result["timestamp"])

        # Determine primary source if IBKR not connected
        if not result["ibkr_connected"]:
            if result["tv"] is not None:
                result["primary_source"] = "tv"
            else:
                result["primary_source"] = "yfinance"
    except Exception as e:
        logger.warning(f"TV/YF validation error: {e}")
        result["warning"] = f"TV/YF validation error: {e}"

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    symbol = sys.argv[1] if len(sys.argv) > 1 else "TSLA"
    print(json.dumps(run_audit(symbol)))

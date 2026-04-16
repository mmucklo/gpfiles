"""
TSLA Alpha Engine: Data Source Audit Aggregator
Runs all data sources (IBKR, TV, yfinance) and returns a combined status dict.

Used by execution_engine/api.go as the /api/data/audit subprocess:
  python -m ingestion.audit TSLA
"""
import json
import os
import sys
import time
import logging

logger = logging.getLogger("DataAudit")


def run_audit(symbol: str = "TSLA") -> dict:
    result = {
        "ibkr_connected":     False,
        "ibkr_spot":          0.0,
        "primary_source":     "yfinance",
        "tv":                 None,
        "yf":                 None,
        "divergence_pct":     0.0,
        "ok":                 True,
        "warning":            None,
        "timestamp":          time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # Chain fields — populated below
        "options_chain_source": os.getenv("OPTIONS_CHAIN_SOURCE", "auto"),
        "chain_entry_count":  0,
        "chain_age_sec":      0,
        "last_chain_fetch":   None,
        "spot_validation":    {},
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
        result["spot_validation"] = val

        # Determine primary source if IBKR not connected
        if not result["ibkr_connected"]:
            if result["tv"] is not None:
                result["primary_source"] = "tv"
            else:
                result["primary_source"] = "yfinance"
    except Exception as e:
        logger.warning(f"TV/YF validation error: {e}")
        result["warning"] = f"TV/YF validation error: {e}"

    # ── 3. Tradier spot cross-validation (4th source) ─────────────────────
    try:
        from ingestion.tradier_chain import get_quotes
        quote = get_quotes(symbol)
        tradier_last = quote.get("last") or 0.0
        if tradier_last and tradier_last > 0:
            yf_price = result.get("yf") or 0.0
            if yf_price and yf_price > 0:
                divergence = abs(tradier_last - yf_price) / yf_price * 100
                if divergence > 1.0:
                    logger.warning(
                        "Tradier spot $%.2f diverges %.2f%% from yfinance $%.2f",
                        tradier_last, divergence, yf_price,
                    )
            result["tradier_spot"] = float(tradier_last)
    except Exception as e:
        logger.debug("Tradier spot cross-validation skipped: %s", e)

    # ── 4. Chain snapshot audit ───────────────────────────────────────────
    try:
        from ingestion.options_chain import get_chain_cache
        chain_cache = get_chain_cache()
        # Use the cached expiry list (no new network call if already fetched)
        expiry_list = chain_cache._expiry_list
        if expiry_list:
            nearest_expiry = expiry_list[0]
            cached = chain_cache._cache.get(nearest_expiry)
            if cached:
                ts, rows = cached
                age_sec = int(time.time() - ts)
                result["chain_entry_count"]  = len(rows)
                result["chain_age_sec"]      = age_sec
                result["last_chain_fetch"]   = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)
                )
                # Determine actual source from the rows
                if rows:
                    sources = {r.greeks_source for r in rows}
                    if "tradier" in sources:
                        result["options_chain_source"] = "tradier"
                    elif "ibkr" in sources:
                        result["options_chain_source"] = "ibkr"
                    elif "computed_bs" in sources:
                        result["options_chain_source"] = "yfinance"
    except Exception as e:
        logger.debug("Chain snapshot audit skipped: %s", e)

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    symbol = sys.argv[1] if len(sys.argv) > 1 else "TSLA"
    print(json.dumps(run_audit(symbol)))

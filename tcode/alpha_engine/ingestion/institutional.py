#!/usr/bin/env python3
"""
TSLA Institutional Flow Tracker: 13F filings + insider transactions.
Feeds into OPTIONS_FLOW model with real institutional data.
"""
import time
import logging
from typing import Optional

logger = logging.getLogger("Institutional")

_inst_cache: Optional[dict] = None
_inst_cache_ts: float = 0.0
_INST_TTL = 3600  # 1 hour — institutional data changes slowly


def _fetch_institutional_holders() -> dict:
    """Fetch top institutional holders and ownership breakdown from yfinance."""
    try:
        import yfinance as yf
        ticker = yf.Ticker("TSLA")

        result = {
            "top_holders": [],
            "total_institutional_pct": 0.0,
            "insider_pct": 0.0,
        }

        # Major holders breakdown
        try:
            major = ticker.major_holders
            if major is not None and not major.empty:
                for _, row in major.iterrows():
                    val = str(row.iloc[0]).strip()
                    label = str(row.iloc[1]).strip() if len(row) > 1 else ""
                    if "insider" in label.lower():
                        try:
                            result["insider_pct"] = float(val.replace("%", ""))
                        except ValueError:
                            pass
                    elif "institution" in label.lower() and "held" in label.lower():
                        try:
                            result["total_institutional_pct"] = float(val.replace("%", ""))
                        except ValueError:
                            pass
        except Exception as e:
            logger.debug(f"Major holders failed: {e}")

        # Top institutional holders
        try:
            inst = ticker.institutional_holders
            if inst is not None and not inst.empty:
                for _, row in inst.head(10).iterrows():
                    holder = {
                        "name": str(row.get("Holder", "Unknown")),
                        "shares": int(row.get("Shares", 0)),
                        "pct_out": float(row.get("% Out", 0.0)) if row.get("% Out") else 0.0,
                        "value": int(row.get("Value", 0)),
                    }
                    result["top_holders"].append(holder)
        except Exception as e:
            logger.debug(f"Institutional holders failed: {e}")

        return result
    except Exception as e:
        logger.warning(f"Institutional holders fetch failed: {e}")
        return {"top_holders": [], "total_institutional_pct": 0.0, "insider_pct": 0.0}


def _fetch_insider_activity() -> dict:
    """Fetch recent insider buy/sell transactions from yfinance."""
    try:
        import yfinance as yf
        ticker = yf.Ticker("TSLA")

        result = {
            "recent_transactions": [],
            "net_insider_sentiment": "NEUTRAL",
            "buy_count": 0,
            "sell_count": 0,
        }

        try:
            insider = ticker.insider_transactions
            if insider is not None and not insider.empty:
                buys = 0
                sells = 0
                for _, row in insider.head(15).iterrows():
                    txn_type = str(row.get("Transaction", "")).lower()
                    is_buy = "purchase" in txn_type or "buy" in txn_type or "acquisition" in txn_type
                    is_sell = "sale" in txn_type or "sell" in txn_type or "disposition" in txn_type

                    import math
                    val = row.get("Value", 0.0)
                    val = 0.0 if (isinstance(val, float) and math.isnan(val)) else float(val)
                    result["recent_transactions"].append({
                        "insider": str(row.get("Insider", "Unknown")),
                        "relation": str(row.get("Insider Relation", "")),
                        "type": "BUY" if is_buy else "SELL" if is_sell else str(row.get("Transaction", "")),
                        "shares": int(row.get("Shares", 0)),
                        "value": val,
                    })

                    if is_buy:
                        buys += 1
                    elif is_sell:
                        sells += 1

                result["buy_count"] = buys
                result["sell_count"] = sells

                if buys > sells * 2:
                    result["net_insider_sentiment"] = "BULLISH"
                elif sells > buys * 2:
                    result["net_insider_sentiment"] = "BEARISH"
        except Exception as e:
            logger.debug(f"Insider transactions failed: {e}")

        return result
    except Exception as e:
        logger.warning(f"Insider activity fetch failed: {e}")
        return {"recent_transactions": [], "net_insider_sentiment": "NEUTRAL", "buy_count": 0, "sell_count": 0}


def get_institutional_intel() -> dict:
    """Return combined institutional holders + insider activity. Cached 1 hour."""
    global _inst_cache, _inst_cache_ts
    now = time.time()

    if _inst_cache is None or now - _inst_cache_ts > _INST_TTL:
        holders = _fetch_institutional_holders()
        insider = _fetch_insider_activity()
        _inst_cache = {**holders, **insider}
        _inst_cache_ts = now
        logger.info(f"Institutional intel refreshed: {len(holders.get('top_holders', []))} holders, "
                     f"{insider.get('buy_count', 0)} insider buys, {insider.get('sell_count', 0)} sells")

    return _inst_cache


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    result = get_institutional_intel()
    print(json.dumps(result, indent=2, default=str))

#!/usr/bin/env python3
"""CLI entry point for fetching options chain data as JSON."""
import json
import sys
import argparse
import logging

# Add parent dirs to path
sys.path.insert(0, "/home/builder/src/gemini/alpha_engine")

from ingestion.options_chain import get_chain_cache

logging.basicConfig(level=logging.WARNING)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expiry", default=None, help="Expiry date YYYY-MM-DD")
    args = parser.parse_args()

    cache = get_chain_cache()
    expiry = args.expiry or cache.nearest_expiry_with_liquidity(min_dte=1)

    if not expiry:
        print(json.dumps({"error": "no liquid expiry found", "expiries": []}))
        return

    rows = cache.get_chain(expiry)
    calls = [r for r in rows if r.option_type == "CALL" and r.open_interest >= 50]
    puts = [r for r in rows if r.option_type == "PUT" and r.open_interest >= 50]

    # Sort by strike
    calls.sort(key=lambda r: r.strike)
    puts.sort(key=lambda r: r.strike)

    def row_to_dict(r):
        return {
            "strike": r.strike,
            "option_type": r.option_type,
            "expiration_date": r.expiration_date,
            "bid": round(r.bid, 2),
            "ask": round(r.ask, 2),
            "mid": round(r.mid_price, 2),
            "last": round(r.last_price, 2),
            "iv": round(r.implied_volatility * 100, 1),
            "oi": r.open_interest,
            "spread_pct": round(r.spread_pct * 100, 1),
        }

    result = {
        "expiry": expiry,
        "expiries": list(cache._get_expiry_list()[:8]),
        "calls": [row_to_dict(r) for r in calls],
        "puts": [row_to_dict(r) for r in puts],
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()

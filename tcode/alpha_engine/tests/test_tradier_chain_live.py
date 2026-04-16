"""
Live integration test for Tradier chain — hits actual Tradier API.

Requires: TRADIER_API_TOKEN env var set to a valid token.

Run with:
  pytest tests/test_tradier_chain_live.py -v -m network

Skips automatically if TRADIER_API_TOKEN is not set.
"""
import sys
import os
import pytest

sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")

pytestmark = pytest.mark.network


def _token_available() -> bool:
    return bool(os.getenv("TRADIER_API_TOKEN", "").strip())


@pytest.fixture(autouse=True)
def require_token():
    if not _token_available():
        pytest.skip("TRADIER_API_TOKEN not set — skipping live test")


class TestTradierLive:
    def test_get_expirations_returns_dates(self):
        from ingestion.tradier_chain import get_expirations
        dates = get_expirations("TSLA")
        assert isinstance(dates, list), "Expected list of expiration date strings"
        assert len(dates) >= 1, "Expected at least 1 expiration date"
        # Dates should be YYYY-MM-DD
        for d in dates[:4]:
            assert len(d) == 10 and d[4] == "-" and d[7] == "-", f"Bad date format: {d}"

    def test_get_chain_has_contracts(self):
        from ingestion.tradier_chain import get_expirations, get_chain
        dates = get_expirations("TSLA")
        assert dates, "No expirations — cannot test chain"

        # Use first upcoming expiry
        expiry = dates[0]
        opts = get_chain("TSLA", expiry)
        assert len(opts) > 50, (
            f"Expected >50 contracts for {expiry}, got {len(opts)}. "
            "Tradier may be returning empty chain — check token or market hours."
        )

    def test_get_chain_greeks_present(self):
        from ingestion.tradier_chain import get_expirations, get_chain
        dates = get_expirations("TSLA")
        assert dates
        expiry = dates[0]
        opts = get_chain("TSLA", expiry)
        assert opts, "Chain is empty"

        # Count contracts with greeks
        with_greeks = [o for o in opts if o.get("greeks") and o["greeks"].get("delta") is not None]
        # At least 70% of contracts should have greeks
        pct = len(with_greeks) / len(opts)
        assert pct >= 0.7, (
            f"Only {len(with_greeks)}/{len(opts)} contracts have greeks ({pct:.0%}). "
            "Tradier should provide native greeks for most liquid contracts."
        )

    def test_get_chain_delta_in_range(self):
        """All native-greek deltas should be in (-1, 1)."""
        from ingestion.tradier_chain import get_expirations, get_chain
        dates = get_expirations("TSLA")
        assert dates
        opts = get_chain("TSLA", dates[0])

        for opt in opts:
            greeks = opt.get("greeks") or {}
            delta = greeks.get("delta")
            if delta is not None:
                assert -1.0 <= float(delta) <= 1.0, f"Delta out of range: {delta}"

    def test_get_chain_has_calls_and_puts(self):
        from ingestion.tradier_chain import get_expirations, get_chain
        dates = get_expirations("TSLA")
        assert dates
        opts = get_chain("TSLA", dates[0])

        types = {o["option_type"] for o in opts}
        assert "call" in types, "No call options returned"
        assert "put"  in types, "No put options returned"

    def test_get_quotes_returns_last_price(self):
        from ingestion.tradier_chain import get_quotes
        quote = get_quotes("TSLA")
        assert isinstance(quote, dict), "Expected dict from get_quotes"
        assert "last" in quote, "Expected 'last' key in quote"
        last = float(quote["last"])
        assert 10.0 < last < 10000.0, f"TSLA price ${last:.2f} out of sanity range"

    def test_option_row_mapping_greeks_source(self):
        """_fetch_chain_tradier maps rows correctly with greeks_source='tradier'."""
        from ingestion.options_chain import OptionsChainCache
        from ingestion.tradier_chain import get_expirations

        dates = get_expirations("TSLA")
        assert dates

        cache = OptionsChainCache("TSLA")
        rows = cache._fetch_chain_tradier(dates[0])
        assert len(rows) > 50, f"Expected >50 OptionRows, got {len(rows)}"

        tradier_rows = [r for r in rows if r.greeks_source == "tradier"]
        assert len(tradier_rows) > 0, "No rows have greeks_source='tradier'"

        # Verify delta range on tradier rows
        for row in tradier_rows:
            if row.delta is not None:
                assert -1.0 <= row.delta <= 1.0, f"Delta {row.delta} out of range"

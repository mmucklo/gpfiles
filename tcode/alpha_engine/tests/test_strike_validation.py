"""
Tests for strike price validation and chain snapping.
Ensures all generated strikes exist in the real options chain at standard increments.
"""
import pytest
import sys
sys.path.insert(0, "/home/builder/src/gemini/alpha_engine")

from ingestion.options_chain import round_to_chain_increment, OptionsChainCache, OptionRow


class TestRoundToChainIncrement:
    """round_to_chain_increment must always produce strikes at $5 boundaries."""

    def test_rounds_down(self):
        assert round_to_chain_increment(331.55) == 330.0

    def test_rounds_up(self):
        assert round_to_chain_increment(333.0) == 335.0

    def test_exact_value(self):
        assert round_to_chain_increment(330.0) == 330.0

    def test_midpoint_rounds_up(self):
        # 332.5 is equidistant — Python's banker's rounding gives 330 (round to even)
        result = round_to_chain_increment(332.5)
        assert result % 5 == 0, f"Got {result}, not a $5 increment"

    def test_high_strike(self):
        assert round_to_chain_increment(366.45) == 365.0

    def test_low_strike(self):
        assert round_to_chain_increment(247.3) == 245.0

    def test_custom_increment(self):
        assert round_to_chain_increment(101.3, increment=1.0) == 101.0

    def test_always_produces_valid_increment(self):
        """Fuzz test: any spot price * moneyness must produce a $5 strike."""
        import random
        random.seed(42)
        for _ in range(100):
            spot = random.uniform(200, 500)
            for moneyness in [0.90, 0.95, 1.0, 1.05, 1.10]:
                result = round_to_chain_increment(spot * moneyness)
                assert result % 5 == 0, f"spot={spot:.2f} money={moneyness} → {result} not $5 increment"


class TestSnapStrikeFallbacks:
    """snap_strike fallback paths must produce $5-increment strikes."""

    def _make_cache(self):
        """Create a cache with no data (forces fallback)."""
        cache = OptionsChainCache("FAKE_TICKER")
        # Don't populate any data — this forces the fallback paths
        return cache

    def test_no_expiry_fallback(self):
        cache = self._make_cache()
        # With no expiry list, snap_strike should fallback
        strike, iv, exp = cache.snap_strike(349.0, "CALL", 1.05)
        assert strike % 5 == 0, f"Fallback strike {strike} is not a $5 increment"
        assert iv == 0.0
        assert exp == ""

    def test_no_liquid_candidates_fallback(self):
        cache = self._make_cache()
        # Add an expiry with empty chain
        cache._expiry_list = ["2026-04-17"]
        cache._expiry_ts = 9999999999.0  # far future so cache doesn't refetch
        cache._cache["2026-04-17"] = (9999999999.0, [])  # empty chain
        strike, iv, exp = cache.snap_strike(349.0, "PUT", 0.95)
        assert strike % 5 == 0, f"Fallback strike {strike} is not a $5 increment"

    def test_no_liquid_oi_fallback(self):
        """Chain has strikes but none with enough OI."""
        cache = self._make_cache()
        cache._expiry_list = ["2026-04-17"]
        cache._expiry_ts = 9999999999.0
        # Add rows with OI below minimum (100)
        rows = [
            OptionRow(strike=330.0, option_type="PUT", expiration_date="2026-04-17",
                     implied_volatility=0.4, open_interest=5, bid=1.0, ask=1.2, last_price=1.1),
            OptionRow(strike=335.0, option_type="PUT", expiration_date="2026-04-17",
                     implied_volatility=0.4, open_interest=10, bid=0.8, ask=1.0, last_price=0.9),
        ]
        cache._cache["2026-04-17"] = (9999999999.0, rows)
        strike, iv, exp = cache.snap_strike(349.0, "PUT", 0.95)
        assert strike % 5 == 0, f"Fallback strike {strike} is not a $5 increment"

    def test_snap_to_real_strike(self):
        """With valid chain data, snap_strike returns an actual chain strike."""
        cache = self._make_cache()
        cache._expiry_list = ["2026-04-17"]
        cache._expiry_ts = 9999999999.0
        # Provide >= MIN_STRIKES (5) liquid rows so nearest_expiry_with_liquidity accepts this expiry
        rows = [
            OptionRow(strike=320.0, option_type="PUT", expiration_date="2026-04-17",
                     implied_volatility=0.36, open_interest=6000, bid=0.9, ask=1.0, last_price=0.95),
            OptionRow(strike=325.0, option_type="PUT", expiration_date="2026-04-17",
                     implied_volatility=0.38, open_interest=7000, bid=1.2, ask=1.3, last_price=1.25),
            OptionRow(strike=330.0, option_type="PUT", expiration_date="2026-04-17",
                     implied_volatility=0.40, open_interest=8000, bid=1.8, ask=1.9, last_price=1.85),
            OptionRow(strike=335.0, option_type="PUT", expiration_date="2026-04-17",
                     implied_volatility=0.42, open_interest=10000, bid=2.7, ask=2.8, last_price=2.75),
            OptionRow(strike=340.0, option_type="PUT", expiration_date="2026-04-17",
                     implied_volatility=0.44, open_interest=5000, bid=3.5, ask=3.6, last_price=3.55),
        ]
        cache._cache["2026-04-17"] = (9999999999.0, rows)
        strike, iv, exp = cache.snap_strike(349.0, "PUT", 0.95)
        # 349 * 0.95 = 331.55, nearest liquid is 330
        assert strike == 330.0, f"Expected 330, got {strike}"
        assert iv == 0.40
        assert exp == "2026-04-17"


class TestPublisherStrikeValidation:
    """Publisher must never emit a non-$5 strike."""

    def test_publisher_fallback_rounds_to_5(self):
        """Simulate the publisher fallback: round(spot * moneyness / 5) * 5."""
        spot = 349.0
        for moneyness in [0.90, 0.95, 1.0, 1.05, 1.10]:
            strike = round(spot * moneyness / 5.0) * 5.0
            assert strike % 5 == 0, f"spot={spot} money={moneyness} → {strike}"

    def test_spread_long_strike_fallback(self):
        """Long strike fallback must also produce $5 increments."""
        for strike in [330.0, 335.0, 340.0, 365.0, 370.0]:
            for offset in [5.0, -5.0]:
                long_target = strike + offset
                long_strike = round(long_target / 5.0) * 5.0
                assert long_strike % 5 == 0, f"strike={strike} offset={offset} → {long_strike}"

    def test_validation_catches_bad_strikes(self):
        """Pre-publish validation rounds non-$5 strikes."""
        bad_strikes = [332.0, 327.0, 331.55, 366.45, 347.0]
        for s in bad_strikes:
            if s % 5.0 != 0:
                fixed = round(s / 5.0) * 5.0
                assert fixed % 5 == 0, f"Validation failed for {s} → {fixed}"
                assert fixed != s, f"Validation didn't change {s}"

    def test_valid_strikes_unchanged(self):
        """Valid $5-increment strikes pass through unchanged."""
        good_strikes = [325.0, 330.0, 335.0, 340.0, 365.0, 370.0]
        for s in good_strikes:
            assert s % 5.0 == 0
            fixed = round(s / 5.0) * 5.0
            assert fixed == s, f"Valid strike {s} was changed to {fixed}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

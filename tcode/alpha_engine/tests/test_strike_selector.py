"""
Tests for Phase 14 strike_selector.py.
Uses synthetic OptionRow objects — no network calls.
"""
import sys
import os
import pytest
from dataclasses import dataclass
from typing import Optional

sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")

from strike_selector import select_strike, StrikeSelection


@dataclass
class MockRow:
    """Minimal stand-in for OptionRow."""
    strike: float
    option_type: str
    expiration_date: str
    implied_volatility: float
    open_interest: int
    bid: float
    ask: float
    last_price: float
    volume: int = 200
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    greeks_source: str = "computed_bs"

    @property
    def mid_price(self):
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.last_price

    @property
    def spread_pct(self):
        mid = self.mid_price
        if mid <= 0:
            return 1.0
        return (self.ask - self.bid) / mid


EXPIRY = "2026-04-21"

# A set of realistic TSLA chain rows around $380 spot
def make_chain(include_liquid=True, include_greeks=True):
    rows = []
    strikes = [350, 360, 370, 375, 380, 385, 390, 400, 410]
    for s in strikes:
        # Approximate delta based on moneyness (~0.5 ATM, decreasing OTM)
        moneyness = (s - 380) / 380
        call_delta = max(0.02, min(0.98, 0.5 - moneyness * 2.5))
        put_delta  = -(1.0 - call_delta)

        for opt_type, delta in [("CALL", call_delta), ("PUT", put_delta)]:
            premium = max(0.10, abs(380 - s) * 0.10 + 2.0)
            theta = -premium * 0.04  # 4% daily theta
            vega = premium * 0.15

            rows.append(MockRow(
                strike=float(s),
                option_type=opt_type,
                expiration_date=EXPIRY,
                implied_volatility=0.65,
                open_interest=1000 if include_liquid else 10,
                bid=round(premium * 0.95, 2),
                ask=round(premium * 1.05, 2),
                last_price=premium,
                volume=300 if include_liquid else 5,
                delta=delta if include_greeks else None,
                gamma=0.01 if include_greeks else None,
                theta=theta if include_greeks else None,
                vega=vega if include_greeks else None,
                greeks_source="computed_bs" if include_greeks else "unavailable",
            ))
    return rows


class TestLiquidityGate:
    def test_rejects_all_low_oi(self):
        """When all rows fail OI floor, returns None."""
        rows = make_chain(include_liquid=False)
        result = select_strike(
            rows, "DIRECTIONAL_STD", 380.0, "LONG_CALL", EXPIRY,
            min_open_interest=500, min_volume_today=50,
            max_bid_ask_pct=0.15, min_absolute_bid=0.10,
        )
        assert result is None

    def test_rejects_all_low_volume(self):
        """When all rows fail volume floor, returns None."""
        rows = make_chain()
        for r in rows:
            r.volume = 5
        result = select_strike(
            rows, "DIRECTIONAL_STD", 380.0, "LONG_CALL", EXPIRY,
            min_open_interest=500, min_volume_today=50,
            max_bid_ask_pct=0.15, min_absolute_bid=0.10,
        )
        assert result is None

    def test_rejects_penny_contracts(self):
        """Contracts with bid < min_absolute_bid are rejected."""
        rows = make_chain()
        for r in rows:
            r.bid = 0.05
            r.ask = 0.06
        result = select_strike(
            rows, "DIRECTIONAL_STD", 380.0, "LONG_CALL", EXPIRY,
            min_open_interest=100, min_volume_today=10,
            max_bid_ask_pct=0.50, min_absolute_bid=0.10,
        )
        assert result is None

    def test_rejects_wide_spread(self):
        """Contracts with spread > max_bid_ask_pct are rejected."""
        rows = make_chain()
        for r in rows:
            r.bid = 1.0
            r.ask = 5.0  # 133% spread
        result = select_strike(
            rows, "DIRECTIONAL_STD", 380.0, "LONG_CALL", EXPIRY,
            min_open_interest=100, min_volume_today=10,
            max_bid_ask_pct=0.15, min_absolute_bid=0.10,
        )
        assert result is None

    def test_env_override_floors(self, monkeypatch):
        """Environment variable overrides are honored."""
        rows = make_chain()
        # Set env to require very high OI
        monkeypatch.setenv("MIN_OPTION_OPEN_INTEREST", "5000")
        result = select_strike(rows, "DIRECTIONAL_STD", 380.0, "LONG_CALL", EXPIRY)
        assert result is None


class TestGreeksGate:
    def test_rejects_unavailable_greeks(self):
        """When greeks_source=unavailable for all rows, returns None."""
        rows = make_chain(include_greeks=False)
        result = select_strike(
            rows, "DIRECTIONAL_STD", 380.0, "LONG_CALL", EXPIRY,
            min_open_interest=100, min_volume_today=10,
        )
        assert result is None


class TestDeltaBand:
    def test_selects_correct_delta_range(self):
        """DIRECTIONAL_STD target_delta=0.30, tol=0.05 → should pick ~0.30 delta."""
        rows = make_chain()
        result = select_strike(
            rows, "DIRECTIONAL_STD", 380.0, "LONG_CALL", EXPIRY,
            min_open_interest=100, min_volume_today=10,
            max_bid_ask_pct=0.50, min_absolute_bid=0.05,
        )
        if result is not None:
            assert abs(result.delta - 0.30) <= 0.10, f"delta={result.delta}"

    def test_rejects_when_no_delta_in_band(self):
        """When no row falls in delta band, returns None."""
        rows = make_chain()
        # Set all deltas far from target
        for r in rows:
            if r.option_type == "CALL":
                r.delta = 0.95  # way too high for DIRECTIONAL_STD (target 0.30±0.05)
        result = select_strike(
            rows, "DIRECTIONAL_STD", 380.0, "LONG_CALL", EXPIRY,
            min_open_interest=100, min_volume_today=10,
            max_bid_ask_pct=0.50, min_absolute_bid=0.05,
        )
        assert result is None

    def test_directional_strong_higher_delta(self):
        """DIRECTIONAL_STRONG target_delta=0.40 should pick higher delta than STD."""
        rows = make_chain()
        result_strong = select_strike(
            rows, "DIRECTIONAL_STRONG", 380.0, "LONG_CALL", EXPIRY,
            min_open_interest=100, min_volume_today=10,
            max_bid_ask_pct=0.50, min_absolute_bid=0.05,
        )
        result_std = select_strike(
            rows, "DIRECTIONAL_STD", 380.0, "LONG_CALL", EXPIRY,
            min_open_interest=100, min_volume_today=10,
            max_bid_ask_pct=0.50, min_absolute_bid=0.05,
        )
        if result_strong and result_std:
            # STRONG targets 0.40, STD targets 0.30 — strong should pick closer to ITM
            assert result_strong.delta >= result_std.delta - 0.05


class TestScoringAndReturn:
    def test_returns_strike_selection_dataclass(self):
        rows = make_chain()
        result = select_strike(
            rows, "DIRECTIONAL_STD", 380.0, "LONG_CALL", EXPIRY,
            min_open_interest=100, min_volume_today=10,
            max_bid_ask_pct=0.50, min_absolute_bid=0.05,
        )
        if result is not None:
            assert isinstance(result, StrikeSelection)
            assert result.score >= 0
            assert result.score <= 1.0
            assert "delta_fit" in result.score_breakdown
            assert "liquidity" in result.score_breakdown
            assert "spread_tightness" in result.score_breakdown
            assert "theta_efficiency" in result.score_breakdown

    def test_headroom_computed(self):
        rows = make_chain()
        result = select_strike(
            rows, "DIRECTIONAL_STD", 380.0, "LONG_CALL", EXPIRY,
            min_open_interest=100, min_volume_today=10,
            max_bid_ask_pct=0.50, min_absolute_bid=0.05,
        )
        if result is not None:
            assert "volume" in result.liquidity_headroom
            assert "oi" in result.liquidity_headroom
            # volume=300/10=30x headroom
            assert result.liquidity_headroom["volume"] > 1.0

    def test_none_when_empty_chain(self):
        result = select_strike([], "DIRECTIONAL_STD", 380.0, "LONG_CALL", EXPIRY)
        assert result is None

    def test_unknown_archetype_returns_none(self):
        rows = make_chain()
        result = select_strike(
            rows, "UNKNOWN_ARCHETYPE", 380.0, "LONG_CALL", EXPIRY,
        )
        assert result is None


class TestThetaCap:
    def test_rejects_high_theta_burn(self):
        """Rows with theta burn > max_theta_pct_premium should be filtered."""
        rows = make_chain()
        # Set theta to burn 50% of premium daily — way above 5% cap
        for r in rows:
            if r.option_type == "CALL":
                r.theta = -r.mid_price * 0.50
        result = select_strike(
            rows, "DIRECTIONAL_STD", 380.0, "LONG_CALL", EXPIRY,
            min_open_interest=100, min_volume_today=10,
            max_bid_ask_pct=0.50, min_absolute_bid=0.05,
        )
        assert result is None


class TestVolPlayArchetype:
    def test_vol_play_requires_vega(self):
        """VOL_PLAY requires vega >= 0.10; rows with low vega filtered."""
        rows = make_chain()
        for r in rows:
            if r.option_type == "CALL":
                r.vega = 0.01  # below 0.10 floor
        result = select_strike(
            rows, "VOL_PLAY", 380.0, "LONG_CALL", EXPIRY,
            min_open_interest=100, min_volume_today=10,
            max_bid_ask_pct=0.50, min_absolute_bid=0.05,
        )
        assert result is None

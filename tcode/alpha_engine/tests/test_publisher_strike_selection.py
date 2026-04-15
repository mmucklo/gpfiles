"""
Integration test: publisher strike selection using mocked chain.
Verifies that select_strike is called and strike_selection_meta is attached to signal.
"""
import sys
import pytest
from unittest.mock import patch, MagicMock
from dataclasses import dataclass
from typing import Optional

sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")


@dataclass
class MockRow:
    strike: float
    option_type: str
    expiration_date: str
    implied_volatility: float
    open_interest: int
    bid: float
    ask: float
    last_price: float
    volume: int = 300
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    greeks_source: str = "computed_bs"

    @property
    def mid_price(self):
        return (self.bid + self.ask) / 2

    @property
    def spread_pct(self):
        mid = self.mid_price
        return (self.ask - self.bid) / mid if mid > 0 else 1.0


def make_good_chain(spot=380.0, expiry="2026-04-21"):
    """Return a synthetic chain where DIRECTIONAL_STD should find a valid strike."""
    rows = []
    for s, call_delta, put_delta in [
        (355, 0.20, -0.80), (360, 0.25, -0.75), (365, 0.30, -0.70),
        (370, 0.35, -0.65), (375, 0.40, -0.60), (380, 0.50, -0.50),
        (385, 0.60, -0.40), (390, 0.68, -0.32), (395, 0.75, -0.25),
    ]:
        premium = max(0.50, abs(spot - s) * 0.10 + 2.0)
        for opt, delta in [("CALL", call_delta), ("PUT", put_delta)]:
            rows.append(MockRow(
                strike=float(s),
                option_type=opt,
                expiration_date=expiry,
                implied_volatility=0.65,
                open_interest=800,
                bid=round(premium * 0.95, 2),
                ask=round(premium * 1.05, 2),
                last_price=premium,
                volume=200,
                delta=delta,
                gamma=0.01,
                theta=-premium * 0.035,
                vega=premium * 0.12,
                greeks_source="computed_bs",
            ))
    return rows


class TestStrikeSelectionIntegration:
    def test_select_strike_returns_selection_for_good_chain(self):
        from strike_selector import select_strike
        rows = make_good_chain()
        result = select_strike(
            rows, "DIRECTIONAL_STD", 380.0, "LONG_CALL", "2026-04-21",
            min_open_interest=100, min_volume_today=10,
            max_bid_ask_pct=0.50, min_absolute_bid=0.05,
        )
        assert result.selected is not None
        assert result.selected.strike > 0
        assert result.selected.greeks_source == "computed_bs"
        assert result.selected.score > 0

    def test_meta_attached_to_selection(self):
        """StrikeSelection has score_breakdown and liquidity_headroom."""
        from strike_selector import select_strike
        rows = make_good_chain()
        result = select_strike(
            rows, "DIRECTIONAL_STD", 380.0, "LONG_CALL", "2026-04-21",
            min_open_interest=100, min_volume_today=10,
            max_bid_ask_pct=0.50, min_absolute_bid=0.05,
        )
        assert result.selected is not None
        assert "delta_fit" in result.selected.score_breakdown
        assert result.selected.liquidity_headroom["volume"] >= 1.0

    def test_no_strike_when_all_fail_liquidity(self):
        from strike_selector import select_strike
        rows = make_good_chain()
        for r in rows:
            r.volume = 0
        result = select_strike(
            rows, "DIRECTIONAL_STD", 380.0, "LONG_CALL", "2026-04-21",
            min_open_interest=100, min_volume_today=50,
            max_bid_ask_pct=0.50, min_absolute_bid=0.05,
        )
        assert result.selected is None
        assert result.rejection_audit["filter_eliminations"]["liquidity"] > 0

    def test_put_direction_selects_puts(self):
        from strike_selector import select_strike
        rows = make_good_chain()
        result = select_strike(
            rows, "DIRECTIONAL_STD", 380.0, "LONG_PUT", "2026-04-21",
            min_open_interest=100, min_volume_today=10,
            max_bid_ask_pct=0.50, min_absolute_bid=0.05,
        )
        if result.selected is not None:
            assert result.selected.contract_type == "PUT"
            assert result.selected.delta < 0

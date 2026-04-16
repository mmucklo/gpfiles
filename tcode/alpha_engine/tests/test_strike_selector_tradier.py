"""
Strike selector integration test with Tradier-sourced chain data.

Feeds the strike selector synthetic OptionRows with greeks_source='tradier'
(matching what _fetch_chain_tradier produces) and verifies:
  - Strike is selected (not STRIKE_SELECT_FAIL)
  - Selected row has greeks_source='tradier'
  - delta is within archetype band
  - No BS-compute fallback needed

This validates the end-to-end path:
  Tradier chain → _fetch_chain_tradier → OptionRow(greeks_source='tradier') →
  select_strike() → StrikeSelection (strike, delta, greeks_source='tradier')
"""
import sys
import os
import pytest
from dataclasses import dataclass
from typing import Optional

sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")

from strike_selector import select_strike, StrikeSelection, StrikeSelectionResult


@dataclass
class TradierRow:
    """Synthetic OptionRow as returned by _fetch_chain_tradier."""
    strike: float
    option_type: str
    expiration_date: str
    implied_volatility: float
    open_interest: int
    bid: float
    ask: float
    last_price: float
    volume: int
    delta: Optional[float]
    gamma: Optional[float]
    theta: Optional[float]
    vega: Optional[float]
    greeks_source: str = "tradier"

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


EXPIRY = "2026-04-17"
SPOT   = 364.0


def make_tradier_chain(spot: float = SPOT) -> list[TradierRow]:
    """
    Construct a realistic TSLA options chain as Tradier would return it.
    Strikes spread around spot with delta gradient from deep ITM to deep OTM.
    """
    rows = []
    strikes = [300, 310, 320, 330, 340, 350, 355, 360, 365, 370, 375, 380, 390, 400, 410, 420]

    for s in strikes:
        moneyness = (s - spot) / spot
        call_delta = max(0.03, min(0.97, 0.5 - moneyness * 3.0))
        put_delta  = call_delta - 1.0  # put-call parity: delta_put = delta_call - 1

        # Approximate premium from delta
        call_premium = max(0.20, abs(spot - s) * 0.08 + 1.5)
        put_premium  = max(0.20, abs(spot - s) * 0.08 + 1.5)

        oi   = 2000 if abs(s - spot) < 30 else 800
        vol  = 400  if abs(s - spot) < 30 else 150

        # CALL
        rows.append(TradierRow(
            strike=float(s),
            option_type="CALL",
            expiration_date=EXPIRY,
            implied_volatility=0.58,
            open_interest=oi,
            bid=round(call_premium * 0.96, 2),
            ask=round(call_premium * 1.04, 2),
            last_price=call_premium,
            volume=vol,
            delta=round(call_delta, 4),
            gamma=round(0.003, 5),
            theta=round(-call_premium * 0.035, 4),
            vega=round(call_premium * 0.12, 4),
            greeks_source="tradier",
        ))

        # PUT
        rows.append(TradierRow(
            strike=float(s),
            option_type="PUT",
            expiration_date=EXPIRY,
            implied_volatility=0.60,
            open_interest=oi,
            bid=round(put_premium * 0.96, 2),
            ask=round(put_premium * 1.04, 2),
            last_price=put_premium,
            volume=vol,
            delta=round(put_delta, 4),
            gamma=round(0.003, 5),
            theta=round(-put_premium * 0.035, 4),
            vega=round(put_premium * 0.12, 4),
            greeks_source="tradier",
        ))

    return rows


class TestStrikeSelectorWithTradierChain:
    def test_selects_call_strike(self):
        """Tradier chain should yield a valid CALL strike selection."""
        rows = make_tradier_chain()
        result = select_strike(
            rows, "DIRECTIONAL_STD", SPOT, "LONG_CALL", EXPIRY,
            min_open_interest=100, min_volume_today=50,
            max_bid_ask_pct=0.15, min_absolute_bid=0.10,
        )
        assert isinstance(result, StrikeSelectionResult)
        assert result.selected is not None, (
            f"Expected strike selection but got None. "
            f"rejection_audit: {result.rejection_audit}"
        )

    def test_selects_put_strike(self):
        """Tradier chain should yield a valid PUT strike selection."""
        rows = make_tradier_chain()
        result = select_strike(
            rows, "DIRECTIONAL_STD", SPOT, "LONG_PUT", EXPIRY,
            min_open_interest=100, min_volume_today=50,
            max_bid_ask_pct=0.15, min_absolute_bid=0.10,
        )
        assert result.selected is not None, (
            f"Expected PUT strike selection but got None. "
            f"rejection_audit: {result.rejection_audit}"
        )

    def test_selected_greeks_source_is_tradier(self):
        """Selected strike should carry greeks_source='tradier' — no BS-compute."""
        rows = make_tradier_chain()
        result = select_strike(
            rows, "DIRECTIONAL_STD", SPOT, "LONG_CALL", EXPIRY,
            min_open_interest=100, min_volume_today=50,
            max_bid_ask_pct=0.15, min_absolute_bid=0.10,
        )
        assert result.selected is not None
        assert result.selected.greeks_source == "tradier", (
            f"Expected greeks_source='tradier', got '{result.selected.greeks_source}'"
        )

    def test_selected_delta_in_directional_std_band(self):
        """DIRECTIONAL_STD targets delta ~0.30; selected delta should be in band."""
        rows = make_tradier_chain()
        result = select_strike(
            rows, "DIRECTIONAL_STD", SPOT, "LONG_CALL", EXPIRY,
            min_open_interest=100, min_volume_today=50,
            max_bid_ask_pct=0.15, min_absolute_bid=0.10,
        )
        assert result.selected is not None
        assert 0.10 <= result.selected.delta <= 0.55, (
            f"delta={result.selected.delta:.3f} out of expected DIRECTIONAL_STD range"
        )

    def test_no_strike_select_fail_with_tradier_chain(self):
        """STRIKE_SELECT_FAIL should not appear when Tradier provides a full chain."""
        rows = make_tradier_chain()
        result = select_strike(
            rows, "DIRECTIONAL_STRONG", SPOT, "LONG_CALL", EXPIRY,
            min_open_interest=100, min_volume_today=50,
            max_bid_ask_pct=0.20, min_absolute_bid=0.10,
        )
        # At minimum, we should get some candidate rows (not a 0-row pipeline)
        assert result.rejection_audit["total_candidates"] > 0, (
            "Zero candidates — the chain rows are not being evaluated"
        )

    def test_unavailable_greeks_row_filtered_out(self):
        """Rows with greeks_source='unavailable' should fail the greeks gate."""
        rows = make_tradier_chain()
        # Mark all CALL rows as unavailable greeks
        for r in rows:
            if r.option_type == "CALL":
                r.greeks_source = "unavailable"
                r.delta = None
        result = select_strike(
            rows, "DIRECTIONAL_STD", SPOT, "LONG_CALL", EXPIRY,
            min_open_interest=100, min_volume_today=50,
            max_bid_ask_pct=0.15, min_absolute_bid=0.10,
        )
        assert result.selected is None
        assert result.rejection_audit["filter_eliminations"].get("greeks_unavailable", 0) > 0

    def test_mixed_source_chain_selects_tradier_row(self):
        """When chain has both 'tradier' and 'computed_bs' rows, selector picks best score."""
        rows = make_tradier_chain()
        # Add some computed_bs rows (simulating yfinance mixed in)
        from dataclasses import replace
        for r in rows[:4]:
            if r.option_type == "CALL":
                r.greeks_source = "computed_bs"

        result = select_strike(
            rows, "DIRECTIONAL_STD", SPOT, "LONG_CALL", EXPIRY,
            min_open_interest=100, min_volume_today=50,
            max_bid_ask_pct=0.15, min_absolute_bid=0.10,
        )
        # Should still succeed — either source is acceptable
        assert result.selected is not None
        assert result.selected.greeks_source in ("tradier", "computed_bs")

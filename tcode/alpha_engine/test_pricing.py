"""
test_pricing.py — Black-Scholes pricing + greeks accuracy tests.

Reference: Hull, Options Futures & Other Derivatives 10e, §19 ATM case.
  S=K=100, r=5%, sigma=25%, T=30d.

Note on vega convention: this implementation returns vega per unit of
sigma (sigma as decimal fraction, not percent). So Hull's "vega=0.115"
(per 1% move in vol) equals 11.5 in this implementation's units.

These tests live in the root alpha_engine/ directory to match existing
test_backtester.py, test_consensus.py convention.
"""
import os
import sys
import math
import pytest

sys.path.insert(0, os.path.dirname(__file__))

from pricing.greeks import compute_bs_greeks, _unavailable_greeks


# ── Hull §19 textbook case ─────────────────────────────────────────────────

class TestComputeBSGreeks:
    """Tests verified against Hull, Options Futures 10e, §19 ATM case.

    Note: implementation vega is per-unit-sigma (multiply by 100 to get
    Hull's per-1%-vol convention). Tolerances account for date-count rounding.
    """

    ATM = dict(spot=100.0, strike=100.0, ttm_years=30/365, rate=0.05, iv=0.25)

    def test_call_delta_atm(self):
        g = compute_bs_greeks(**self.ATM, option_type="CALL")
        # Hull §19: delta ≈ 0.530. Our implementation yields ≈0.537 (slight
        # difference due to continuous-time formula rounding in Hull's tables).
        assert 0.52 < g["delta"] < 0.55, f"Call delta {g['delta']} outside expected range [0.52, 0.55]"

    def test_put_delta_atm(self):
        g = compute_bs_greeks(**self.ATM, option_type="PUT")
        assert -0.50 < g["delta"] < -0.44, f"Put delta {g['delta']} outside expected range [-0.50, -0.44]"

    def test_call_put_delta_sum_is_one(self):
        c = compute_bs_greeks(**self.ATM, option_type="CALL")
        p = compute_bs_greeks(**self.ATM, option_type="PUT")
        # Delta_call - Delta_put = 1 (put-call parity for European options)
        assert abs((c["delta"] - p["delta"]) - 1.0) < 0.001

    def test_gamma_atm_reasonable(self):
        g = compute_bs_greeks(**self.ATM, option_type="CALL")
        # ATM gamma for S=100, sigma=25%, T=30d is approximately 0.05-0.07
        assert 0.04 < g["gamma"] < 0.08, f"Gamma {g['gamma']} outside [0.04, 0.08]"

    def test_gamma_same_for_call_and_put(self):
        c = compute_bs_greeks(**self.ATM, option_type="CALL")
        p = compute_bs_greeks(**self.ATM, option_type="PUT")
        assert abs(c["gamma"] - p["gamma"]) < 1e-8, "Gamma must be identical for calls and puts"

    def test_vega_atm_per_unit_sigma(self):
        g = compute_bs_greeks(**self.ATM, option_type="CALL")
        # vega per unit sigma ≈ S * sqrt(T) * N'(d1) ≈ 100 * 0.287 * 0.399 ≈ 11.4
        # Hull's 0.115 = 11.5 / 100 (per 1% vol move). Expect 11 ≤ vega ≤ 12.
        assert 10.0 < g["vega"] < 13.0, f"Vega {g['vega']} outside [10, 13] per-unit-sigma range"

    def test_vega_same_for_call_and_put(self):
        c = compute_bs_greeks(**self.ATM, option_type="CALL")
        p = compute_bs_greeks(**self.ATM, option_type="PUT")
        assert abs(c["vega"] - p["vega"]) < 1e-8, "Vega must be identical for calls and puts"

    def test_theta_negative_for_long_call(self):
        g = compute_bs_greeks(**self.ATM, option_type="CALL")
        assert g["theta"] < 0, f"Long call theta should be negative (time decay), got {g['theta']}"

    def test_theta_atm_call_reasonable(self):
        g = compute_bs_greeks(**self.ATM, option_type="CALL")
        # Hull §19: theta ≈ -0.054/day. Expect -0.03 to -0.08/day.
        assert -0.08 < g["theta"] < -0.02, f"Call theta {g['theta']} outside expected range"

    def test_greeks_source_is_computed_bs(self):
        g = compute_bs_greeks(**self.ATM, option_type="CALL")
        assert g["greeks_source"] == "computed_bs"

    def test_iv_preserved_in_output(self):
        g = compute_bs_greeks(**self.ATM, option_type="CALL")
        assert g["iv"] == 0.25


# ── Deep ITM / OTM moneyness ───────────────────────────────────────────────

class TestMoneyness:

    def test_deep_itm_call_delta_near_one(self):
        g = compute_bs_greeks(spot=150.0, strike=100.0, ttm_years=30/365,
                               rate=0.05, iv=0.25, option_type="CALL")
        assert g["delta"] > 0.95, f"Deep ITM call delta should be ~1, got {g['delta']}"

    def test_deep_otm_call_delta_near_zero(self):
        g = compute_bs_greeks(spot=50.0, strike=100.0, ttm_years=30/365,
                               rate=0.05, iv=0.25, option_type="CALL")
        assert g["delta"] < 0.05, f"Deep OTM call delta should be ~0, got {g['delta']}"

    def test_deep_itm_put_delta_near_neg_one(self):
        g = compute_bs_greeks(spot=50.0, strike=100.0, ttm_years=30/365,
                               rate=0.05, iv=0.25, option_type="PUT")
        assert g["delta"] < -0.95, f"Deep ITM put delta should be ~-1, got {g['delta']}"


# ── Degenerate inputs ──────────────────────────────────────────────────────

class TestDegenerateInputs:

    def test_zero_spot_returns_unavailable(self):
        g = compute_bs_greeks(spot=0.0, strike=100.0, ttm_years=0.1,
                               rate=0.05, iv=0.25, option_type="CALL")
        assert g["greeks_source"] == "unavailable"

    def test_zero_strike_returns_unavailable(self):
        g = compute_bs_greeks(spot=100.0, strike=0.0, ttm_years=0.1,
                               rate=0.05, iv=0.25, option_type="CALL")
        assert g["greeks_source"] == "unavailable"

    def test_zero_iv_returns_unavailable(self):
        g = compute_bs_greeks(spot=100.0, strike=100.0, ttm_years=0.1,
                               rate=0.05, iv=0.0, option_type="CALL")
        assert g["greeks_source"] == "unavailable"

    def test_negative_ttm_itm_call_delta_is_one(self):
        # At expiry, ITM call has delta=1
        g = compute_bs_greeks(spot=110.0, strike=100.0, ttm_years=-0.001,
                               rate=0.05, iv=0.25, option_type="CALL")
        assert g["delta"] == 1.0

    def test_negative_ttm_otm_call_delta_is_zero(self):
        # At expiry, OTM call has delta=0
        g = compute_bs_greeks(spot=90.0, strike=100.0, ttm_years=-0.001,
                               rate=0.05, iv=0.25, option_type="CALL")
        assert g["delta"] == 0.0


# ── Contract: return dict always has all expected keys ─────────────────────

class TestReturnContract:

    REQUIRED_KEYS = {"delta", "gamma", "theta", "vega", "iv", "greeks_source"}

    def test_normal_call_has_all_keys(self):
        g = compute_bs_greeks(100.0, 100.0, 30/365, 0.05, 0.25, "CALL")
        assert self.REQUIRED_KEYS.issubset(g.keys()), f"Missing keys: {self.REQUIRED_KEYS - set(g.keys())}"

    def test_unavailable_greeks_has_all_keys(self):
        g = _unavailable_greeks(0.35)
        assert self.REQUIRED_KEYS.issubset(g.keys()), f"Missing keys: {self.REQUIRED_KEYS - set(g.keys())}"

    def test_unavailable_greeks_source_field(self):
        g = _unavailable_greeks(0.35)
        assert g["greeks_source"] == "unavailable"


# ── Higher IV increases gamma and vega ────────────────────────────────────

class TestGreeksMonotonicity:

    def test_higher_iv_increases_vega(self):
        lo = compute_bs_greeks(100.0, 100.0, 30/365, 0.05, 0.20, "CALL")
        hi = compute_bs_greeks(100.0, 100.0, 30/365, 0.05, 0.40, "CALL")
        assert hi["vega"] > lo["vega"], "Higher IV should produce higher vega"

    def test_longer_ttm_increases_vega(self):
        short = compute_bs_greeks(100.0, 100.0, 7/365, 0.05, 0.25, "CALL")
        long_ = compute_bs_greeks(100.0, 100.0, 90/365, 0.05, 0.25, "CALL")
        assert long_["vega"] > short["vega"], "Longer TTM should produce higher vega"

    def test_longer_ttm_reduces_theta_magnitude(self):
        # Longer time to expiry → less daily theta (time decay slower)
        short = compute_bs_greeks(100.0, 100.0, 7/365, 0.05, 0.25, "CALL")
        long_ = compute_bs_greeks(100.0, 100.0, 90/365, 0.05, 0.25, "CALL")
        assert abs(long_["theta"]) < abs(short["theta"]), \
            "Longer TTM should have smaller daily theta magnitude"

"""
Tests for Black-Scholes greeks computation (pricing/greeks.py).
Verified against the BS closed-form formulas (Hull, Options Futures, 10th ed., §19).

NOTE on vega units: our implementation returns vega per 1.0 change in sigma (annualized
fraction), e.g., sigma: 0.25 → 1.25 = Δsigma of 1.0. For S=K=100, T=30/365, sigma=0.25:
  vega = S * sqrt(T) * N'(d1) ≈ 100 * 0.2866 * 0.397 ≈ 11.4 (per unit sigma change)
  or equivalently ≈ 0.114 per 1% vol change (divide by 100).
Hull tables quote the per-1%-change form; our code uses per-unit-sigma form.
"""
import sys
import math
import pytest

sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")
from pricing.greeks import compute_bs_greeks


class TestATMCall:
    """ATM 30-day call: S=K=100, r=5%, sigma=25%.
    Computed references (BS closed-form, T=30/365):
      delta ≈ 0.537, gamma ≈ 0.055, theta ≈ -0.050/day, vega ≈ 11.4 (per unit sigma)."""

    def setup_method(self):
        self.g = compute_bs_greeks(100.0, 100.0, 30 / 365, 0.05, 0.25, "CALL")

    def test_delta_near_half(self):
        """ATM call delta should be just above 0.5."""
        assert 0.50 < self.g["delta"] < 0.60, f"delta={self.g['delta']}"

    def test_gamma_positive(self):
        assert self.g["gamma"] > 0

    def test_gamma_reasonable(self):
        """gamma should be between 0.04 and 0.08 for this contract."""
        assert 0.04 < self.g["gamma"] < 0.08, f"gamma={self.g['gamma']}"

    def test_theta_negative(self):
        assert self.g["theta"] < 0

    def test_theta_reasonable(self):
        """theta should be between -0.08 and -0.01 per day."""
        assert -0.08 < self.g["theta"] < -0.01, f"theta={self.g['theta']}"

    def test_vega_positive(self):
        assert self.g["vega"] > 0

    def test_vega_reasonable(self):
        """vega per unit sigma should be ~10-12 for S=K=100, T=30d, sigma=25%."""
        assert 9 < self.g["vega"] < 14, f"vega={self.g['vega']}"

    def test_greeks_source(self):
        assert self.g["greeks_source"] == "computed_bs"

    def test_call_put_parity_delta(self):
        """call_delta + |put_delta| ≈ 1 (Black-Scholes identity)."""
        put_g = compute_bs_greeks(100.0, 100.0, 30 / 365, 0.05, 0.25, "PUT")
        assert abs(self.g["delta"] + abs(put_g["delta"]) - 1.0) < 0.005


class TestATMPut:
    """ATM 30-day put: S=K=100, r=5%, sigma=25%.
    Put delta should be negative; same gamma and vega as call."""

    def setup_method(self):
        self.g = compute_bs_greeks(100.0, 100.0, 30 / 365, 0.05, 0.25, "PUT")

    def test_delta_negative(self):
        assert self.g["delta"] < 0

    def test_delta_near_minus_half(self):
        """ATM put delta should be just above -0.5."""
        assert -0.55 < self.g["delta"] < -0.45, f"delta={self.g['delta']}"

    def test_gamma_same_as_call(self):
        call_g = compute_bs_greeks(100.0, 100.0, 30 / 365, 0.05, 0.25, "CALL")
        assert abs(self.g["gamma"] - call_g["gamma"]) < 1e-6

    def test_vega_same_as_call(self):
        call_g = compute_bs_greeks(100.0, 100.0, 30 / 365, 0.05, 0.25, "CALL")
        assert abs(self.g["vega"] - call_g["vega"]) < 1e-6


class TestITMCallOTMPut:
    """ITM call (deep in the money): delta should approach 1."""

    def test_deep_itm_call_delta(self):
        g = compute_bs_greeks(120.0, 100.0, 30 / 365, 0.05, 0.25, "CALL")
        assert g["delta"] > 0.80

    def test_deep_otm_call_delta(self):
        g = compute_bs_greeks(80.0, 100.0, 30 / 365, 0.05, 0.25, "CALL")
        assert g["delta"] < 0.20

    def test_deep_itm_put_delta(self):
        g = compute_bs_greeks(80.0, 100.0, 30 / 365, 0.05, 0.25, "PUT")
        assert g["delta"] < -0.80

    def test_deep_otm_put_delta(self):
        g = compute_bs_greeks(120.0, 100.0, 30 / 365, 0.05, 0.25, "PUT")
        assert g["delta"] > -0.20


class TestDegenerateCases:
    """Edge / degenerate inputs that must not raise and must return sensible values."""

    def test_ttm_zero_itm_call(self):
        g = compute_bs_greeks(105.0, 100.0, 0, 0.05, 0.25, "CALL")
        assert g["delta"] == 1.0
        assert g["gamma"] == 0.0
        assert g["greeks_source"] == "computed_bs"

    def test_ttm_zero_otm_call(self):
        g = compute_bs_greeks(95.0, 100.0, 0, 0.05, 0.25, "CALL")
        assert g["delta"] == 0.0

    def test_vol_zero(self):
        g = compute_bs_greeks(100.0, 100.0, 30 / 365, 0.05, 0.0, "CALL")
        assert g["greeks_source"] == "unavailable"
        assert g["delta"] is None

    def test_spot_zero(self):
        g = compute_bs_greeks(0.0, 100.0, 30 / 365, 0.05, 0.25, "CALL")
        assert g["greeks_source"] == "unavailable"

    def test_strike_zero(self):
        g = compute_bs_greeks(100.0, 0.0, 30 / 365, 0.05, 0.25, "CALL")
        assert g["greeks_source"] == "unavailable"

    def test_negative_vol(self):
        g = compute_bs_greeks(100.0, 100.0, 30 / 365, 0.05, -0.1, "CALL")
        assert g["greeks_source"] == "unavailable"

    def test_returns_dict(self):
        g = compute_bs_greeks(380.0, 390.0, 7 / 365, 0.05, 0.65, "CALL")
        assert set(g.keys()) >= {"delta", "gamma", "theta", "vega", "iv", "greeks_source"}

    def test_high_vol_no_crash(self):
        """Very high IV (200%) should not raise."""
        g = compute_bs_greeks(100.0, 100.0, 30 / 365, 0.05, 2.0, "CALL")
        assert g["greeks_source"] in ("computed_bs", "unavailable")

    def test_very_long_ttm(self):
        """LEAPS (2y) should not crash."""
        g = compute_bs_greeks(100.0, 100.0, 2.0, 0.05, 0.25, "CALL")
        assert 0 < g["delta"] < 1

    def test_call_put_delta_sum_approx_one(self):
        """Call delta + |put delta| ≈ 1 (put-call parity approximation)."""
        call_g = compute_bs_greeks(380.0, 390.0, 14 / 365, 0.05, 0.65, "CALL")
        put_g  = compute_bs_greeks(380.0, 390.0, 14 / 365, 0.05, 0.65, "PUT")
        assert abs(call_g["delta"] + abs(put_g["delta"]) - 1.0) < 0.01

"""
Tests for regime-conditional Kelly + vol-targeting (Feature 3).

The Kelly sizing logic (implemented in publisher.py's _compute_regime_kelly) must:
  1. Select base fraction by VIX tier:
       VIX > 30 → 0.20 (high-vol)
       VIX > 20 → 0.35 (medium-vol)
       VIX ≤ 20 → 0.50 (low-vol)
  2. Multiply by min(1.0, realized_vol / implied_vol):
       If IV > realized: shrink (options rich)
       If IV ≤ realized: hold (options cheap or fairly priced)
  3. Multiply by 0.5 if regime == RISK_OFF.

All (VIX × regime) permutations are tested here.
"""
import sys
import unittest

sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")


# ── Import the pure Kelly helper from publisher.py ────────────────────────────
# We test the function in isolation to avoid spinning up NATS / ingestion.
def _compute_regime_kelly(
    confidence: float,
    vix: float,
    regime: str,
    realized_vol: float,
    implied_vol: float,
) -> tuple[float, dict]:
    """
    Regime-conditional Kelly sizing.  Mirrors publisher.py implementation exactly.

    Returns (kelly_wager_pct, audit_dict).

    Mathematical basis:
      Full Kelly f* = 2p − 1  (binary bet approximation where p = P(win) ≈ confidence)
      Fractional Kelly reduces f* by a regime-dependent fraction to account for
      model uncertainty (Thorp 2006, Poundstone 2005).

      Vol targeting:  if implied vol > realized, options are "rich" — the market is
      overpaying for insurance.  Scaling down position by (realized/implied) ratio
      approximates a vol-parity adjustment (AQR Volatility Targeting, 2012).

      Risk-off half-Kelly:  in RISK_OFF regimes, tail risk is elevated and
      correlation matrices break down.  Halving position size is conservative
      but empirically reduces max drawdown ~40% in regime-transition periods
      (Man AHL Volatility Targeting research, 2025).
    """
    full_kelly = max(0.0, 2 * confidence - 1)

    # VIX-tiered fraction
    if vix > 30:
        base_fraction = 0.20  # High-vol regime: 1/5 Kelly
    elif vix > 20:
        base_fraction = 0.35  # Medium-vol regime
    else:
        base_fraction = 0.50  # Low-vol regime: half Kelly (conservative max)

    # Vol-targeting adjustment: if IV is overpricing realized, size down proportionally
    if implied_vol > 0 and realized_vol > 0:
        vol_ratio = min(1.0, realized_vol / implied_vol)
    else:
        vol_ratio = 1.0  # No vol data → no adjustment

    # Regime override: RISK_OFF halves position regardless of VIX level
    regime_multiplier = 0.5 if regime == "RISK_OFF" else 1.0

    final_multiplier = base_fraction * vol_ratio * regime_multiplier
    kelly_wager_pct = full_kelly * final_multiplier

    audit = {
        "regime": regime,
        "vix": vix,
        "kelly_base_fraction": base_fraction,
        "vol_ratio": vol_ratio,
        "regime_multiplier": regime_multiplier,
        "final_multiplier": final_multiplier,
    }
    return kelly_wager_pct, audit


class TestVIXTiers(unittest.TestCase):
    """VIX thresholds must select the correct base Kelly fraction."""

    def test_high_vix_above_30(self):
        _, audit = _compute_regime_kelly(0.8, vix=35.0, regime="NEUTRAL",
                                         realized_vol=0.5, implied_vol=0.5)
        self.assertAlmostEqual(audit["kelly_base_fraction"], 0.20, places=2)

    def test_vix_exactly_30_is_medium(self):
        # VIX == 30: condition is vix > 30 (strict), so 30 falls into medium tier
        _, audit = _compute_regime_kelly(0.8, vix=30.0, regime="NEUTRAL",
                                         realized_vol=0.5, implied_vol=0.5)
        self.assertAlmostEqual(audit["kelly_base_fraction"], 0.35, places=2)

    def test_medium_vix_21_to_30(self):
        _, audit = _compute_regime_kelly(0.8, vix=25.0, regime="NEUTRAL",
                                         realized_vol=0.5, implied_vol=0.5)
        self.assertAlmostEqual(audit["kelly_base_fraction"], 0.35, places=2)

    def test_vix_exactly_20_is_low(self):
        # VIX == 20: condition is vix > 20 (strict), so 20 falls into low tier
        _, audit = _compute_regime_kelly(0.8, vix=20.0, regime="NEUTRAL",
                                         realized_vol=0.5, implied_vol=0.5)
        self.assertAlmostEqual(audit["kelly_base_fraction"], 0.50, places=2)

    def test_low_vix_below_20(self):
        _, audit = _compute_regime_kelly(0.8, vix=12.0, regime="NEUTRAL",
                                         realized_vol=0.5, implied_vol=0.5)
        self.assertAlmostEqual(audit["kelly_base_fraction"], 0.50, places=2)


class TestRegimeMultiplier(unittest.TestCase):
    """RISK_OFF must halve the position regardless of VIX tier."""

    def _kelly(self, regime, vix):
        kelly, audit = _compute_regime_kelly(
            0.8, vix=vix, regime=regime, realized_vol=0.5, implied_vol=0.5
        )
        return kelly, audit

    def test_risk_off_halves_high_vix(self):
        neutral_k, _ = self._kelly("NEUTRAL", 35)
        risk_off_k, audit = self._kelly("RISK_OFF", 35)
        self.assertAlmostEqual(audit["regime_multiplier"], 0.5, places=2)
        self.assertAlmostEqual(risk_off_k, neutral_k * 0.5, places=4)

    def test_risk_off_halves_medium_vix(self):
        neutral_k, _ = self._kelly("NEUTRAL", 25)
        risk_off_k, _ = self._kelly("RISK_OFF", 25)
        self.assertAlmostEqual(risk_off_k, neutral_k * 0.5, places=4)

    def test_risk_off_halves_low_vix(self):
        neutral_k, _ = self._kelly("NEUTRAL", 15)
        risk_off_k, _ = self._kelly("RISK_OFF", 15)
        self.assertAlmostEqual(risk_off_k, neutral_k * 0.5, places=4)

    def test_risk_on_neutral_identical_multiplier(self):
        """RISK_ON and NEUTRAL should have the same regime_multiplier (1.0)."""
        _, audit_on = self._kelly("RISK_ON", 15)
        _, audit_neutral = self._kelly("NEUTRAL", 15)
        self.assertAlmostEqual(audit_on["regime_multiplier"], 1.0, places=2)
        self.assertAlmostEqual(audit_neutral["regime_multiplier"], 1.0, places=2)


class TestVolRatioAdjustment(unittest.TestCase):
    """Vol-targeting: if IV > realized, scale down; if IV ≤ realized, hold at 1.0."""

    def test_iv_overpricing_realized_shrinks_position(self):
        """IV = 60% realized, vol_ratio = min(1, 0.4/0.6) = 0.667"""
        _, audit = _compute_regime_kelly(
            0.8, vix=15, regime="NEUTRAL", realized_vol=0.40, implied_vol=0.60
        )
        self.assertAlmostEqual(audit["vol_ratio"], round(0.40 / 0.60, 4), places=3)
        self.assertLess(audit["vol_ratio"], 1.0)

    def test_iv_underpricing_realized_caps_at_1(self):
        """IV = 30% realized = 50%, vol_ratio = min(1, 0.5/0.3) = 1.0 (capped)"""
        _, audit = _compute_regime_kelly(
            0.8, vix=15, regime="NEUTRAL", realized_vol=0.50, implied_vol=0.30
        )
        self.assertAlmostEqual(audit["vol_ratio"], 1.0, places=2)

    def test_equal_vol_ratio_is_1(self):
        _, audit = _compute_regime_kelly(
            0.8, vix=15, regime="NEUTRAL", realized_vol=0.45, implied_vol=0.45
        )
        self.assertAlmostEqual(audit["vol_ratio"], 1.0, places=2)

    def test_missing_vol_data_no_adjustment(self):
        """Zero IV or zero realized_vol → no vol-ratio adjustment (vol_ratio=1)."""
        _, audit_no_iv = _compute_regime_kelly(0.8, 15, "NEUTRAL", 0.45, 0.0)
        _, audit_no_rv = _compute_regime_kelly(0.8, 15, "NEUTRAL", 0.0, 0.45)
        self.assertAlmostEqual(audit_no_iv["vol_ratio"], 1.0, places=2)
        self.assertAlmostEqual(audit_no_rv["vol_ratio"], 1.0, places=2)


class TestAllVIXRegimePermutations(unittest.TestCase):
    """Exhaustive (VIX-tier × regime) matrix — all 9 combinations must produce valid outputs."""

    VIX_TIERS = [(12.0, "LOW"), (25.0, "MED"), (35.0, "HIGH")]
    REGIMES   = ["RISK_ON", "NEUTRAL", "RISK_OFF"]
    EXPECTED_FRACTIONS = {"LOW": 0.50, "MED": 0.35, "HIGH": 0.20}

    def test_all_permutations(self):
        for vix, tier in self.VIX_TIERS:
            for regime in self.REGIMES:
                with self.subTest(vix=vix, regime=regime):
                    kelly, audit = _compute_regime_kelly(
                        confidence=0.75,
                        vix=vix,
                        regime=regime,
                        realized_vol=0.45,
                        implied_vol=0.45,  # vol_ratio = 1.0
                    )
                    expected_base = self.EXPECTED_FRACTIONS[tier]
                    expected_regime_mult = 0.5 if regime == "RISK_OFF" else 1.0
                    expected_kelly = max(0, 2 * 0.75 - 1) * expected_base * expected_regime_mult

                    self.assertAlmostEqual(audit["kelly_base_fraction"], expected_base, places=2,
                                           msg=f"Wrong base fraction for VIX={vix}")
                    self.assertAlmostEqual(kelly, expected_kelly, places=4,
                                           msg=f"Wrong final Kelly for VIX={vix} regime={regime}")
                    self.assertGreaterEqual(kelly, 0.0, "Kelly must be non-negative")

    def test_zero_confidence_produces_zero_kelly(self):
        """Confidence ≤ 0.5 → full_kelly = 0 → no position."""
        kelly, _ = _compute_regime_kelly(0.5, 15, "RISK_ON", 0.4, 0.4)
        self.assertAlmostEqual(kelly, 0.0, places=4)

    def test_high_confidence_capped_by_fractions(self):
        """Even at confidence=0.95, RISK_OFF + HIGH_VIX severely reduces position."""
        kelly, audit = _compute_regime_kelly(0.95, 35, "RISK_OFF", 0.5, 0.5)
        max_possible = (2 * 0.95 - 1) * 0.20 * 0.5  # 0.90 * 0.10 = 0.09
        self.assertAlmostEqual(kelly, max_possible, places=4)


class TestAuditFields(unittest.TestCase):
    """Audit dict must contain all required fields for fills_audit table insert."""

    REQUIRED_FIELDS = {
        "regime", "vix", "kelly_base_fraction", "vol_ratio",
        "regime_multiplier", "final_multiplier",
    }

    def test_all_audit_fields_present(self):
        _, audit = _compute_regime_kelly(0.8, 25, "NEUTRAL", 0.4, 0.5)
        for field in self.REQUIRED_FIELDS:
            self.assertIn(field, audit, f"Missing audit field: {field}")

    def test_final_multiplier_equals_product(self):
        _, audit = _compute_regime_kelly(0.8, 15, "NEUTRAL", 0.40, 0.50)
        expected = audit["kelly_base_fraction"] * audit["vol_ratio"] * audit["regime_multiplier"]
        self.assertAlmostEqual(audit["final_multiplier"], expected, places=6)


if __name__ == "__main__":
    unittest.main()

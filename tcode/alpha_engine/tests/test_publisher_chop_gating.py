"""
Tests for Phase 14 chop-regime gating in publisher.py.
Verifies that CHOPPY blocks DIRECTIONAL, MIXED applies ×0.7, TRENDING is unchanged.
Tests the publisher-level logic directly (not the broadcaster loop).
"""
import sys
import pytest

sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")

from publisher import (
    _CHOP_BLOCK_ARCHETYPES,
    _CHOP_MIXED_MULT,
)


class TestChopGatingConstants:
    def test_chop_block_includes_directional(self):
        assert "DIRECTIONAL_STRONG" in _CHOP_BLOCK_ARCHETYPES
        assert "DIRECTIONAL_STD" in _CHOP_BLOCK_ARCHETYPES

    def test_chop_block_includes_mean_revert(self):
        assert "MEAN_REVERT" in _CHOP_BLOCK_ARCHETYPES

    def test_chop_block_includes_scalp_0dte(self):
        assert "SCALP_0DTE" in _CHOP_BLOCK_ARCHETYPES

    def test_vol_play_not_in_block_set(self):
        """VOL_PLAY is NOT unconditionally blocked — it has conditional logic."""
        assert "VOL_PLAY" not in _CHOP_BLOCK_ARCHETYPES

    def test_mixed_multipliers_directional(self):
        assert _CHOP_MIXED_MULT["DIRECTIONAL_STD"] == 0.7
        assert _CHOP_MIXED_MULT["DIRECTIONAL_STRONG"] == 0.7

    def test_mixed_multipliers_scalp(self):
        assert _CHOP_MIXED_MULT["SCALP_0DTE"] == 0.6

    def test_mixed_multipliers_vol_play_boost(self):
        """VOL_PLAY gets a confidence boost in MIXED regime."""
        assert _CHOP_MIXED_MULT["VOL_PLAY"] > 1.0


class TestChopGatingLogic:
    """Unit tests for the gating decision logic (extracted from broadcast_loop)."""

    def _apply_chop_gate(self, archetype_name, chop_label, chop_score,
                          rv_iv_ratio=1.0, initial_confidence=0.75):
        """
        Simulate the chop-gating section of broadcast_loop.
        Returns (blocked: bool, confidence_after: float).
        """
        confidence = initial_confidence
        blocked = False

        if chop_label == "CHOPPY":
            if archetype_name in _CHOP_BLOCK_ARCHETYPES:
                blocked = True
            elif archetype_name == "VOL_PLAY" and rv_iv_ratio < 0.7:
                blocked = True
        elif chop_label == "MIXED":
            mult = _CHOP_MIXED_MULT.get(archetype_name, 0.7)
            confidence = min(0.95, confidence * mult)

        return blocked, confidence

    def test_choppy_blocks_directional_strong(self):
        blocked, _ = self._apply_chop_gate("DIRECTIONAL_STRONG", "CHOPPY", 0.75)
        assert blocked

    def test_choppy_blocks_directional_std(self):
        blocked, _ = self._apply_chop_gate("DIRECTIONAL_STD", "CHOPPY", 0.75)
        assert blocked

    def test_choppy_blocks_mean_revert(self):
        blocked, _ = self._apply_chop_gate("MEAN_REVERT", "CHOPPY", 0.75)
        assert blocked

    def test_choppy_blocks_scalp_0dte(self):
        blocked, _ = self._apply_chop_gate("SCALP_0DTE", "CHOPPY", 0.75)
        assert blocked

    def test_choppy_blocks_vol_play_when_rv_iv_low(self):
        """VOL_PLAY blocked when rv_iv_ratio < 0.7 (IV too rich)."""
        blocked, _ = self._apply_chop_gate("VOL_PLAY", "CHOPPY", 0.75, rv_iv_ratio=0.5)
        assert blocked

    def test_choppy_allows_vol_play_when_rv_iv_ok(self):
        """VOL_PLAY NOT blocked when rv_iv_ratio >= 0.7."""
        blocked, _ = self._apply_chop_gate("VOL_PLAY", "CHOPPY", 0.75, rv_iv_ratio=0.8)
        assert not blocked

    def test_mixed_downweights_directional(self):
        _, conf = self._apply_chop_gate("DIRECTIONAL_STD", "MIXED", 0.5, initial_confidence=0.80)
        assert abs(conf - 0.80 * 0.7) < 0.001

    def test_mixed_boosts_vol_play(self):
        _, conf = self._apply_chop_gate("VOL_PLAY", "MIXED", 0.5, initial_confidence=0.75)
        assert conf > 0.75  # boosted

    def test_trending_no_adjustment(self):
        blocked, conf = self._apply_chop_gate("DIRECTIONAL_STD", "TRENDING", 0.1, initial_confidence=0.80)
        assert not blocked
        assert conf == 0.80  # unchanged

    def test_mixed_confidence_capped_at_095(self):
        _, conf = self._apply_chop_gate("VOL_PLAY", "MIXED", 0.5, initial_confidence=0.92)
        assert conf <= 0.95

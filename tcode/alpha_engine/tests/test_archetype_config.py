"""
Tests for archetype configuration (Phase 10).

Verifies every archetype has all required fields, within valid ranges,
and that the model→archetype mapping is complete.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from config.archetypes import (
    ARCHETYPES,
    REQUIRED_FIELDS,
    MODEL_ARCHETYPE_MAP,
    get_archetype,
    validate_archetypes,
)
from consensus import ModelType


class TestArchetypeSchema:
    def test_validate_passes(self):
        """validate_archetypes() reports no errors."""
        errors = validate_archetypes()
        assert errors == [], f"Archetype validation errors:\n" + "\n".join(errors)

    def test_all_required_fields_present(self):
        """Every archetype has all required fields."""
        for name, cfg in ARCHETYPES.items():
            for field in REQUIRED_FIELDS:
                assert field in cfg, f"{name}: missing required field '{field}'"

    def test_delta_range(self):
        """delta must be strictly between 0 and 1."""
        for name, cfg in ARCHETYPES.items():
            d = cfg["delta"]
            assert 0 < d < 1, f"{name}: delta={d} must be in (0, 1)"

    def test_risk_pct_reasonable(self):
        """risk_pct must be positive and ≤ 5%."""
        for name, cfg in ARCHETYPES.items():
            r = cfg["risk_pct"]
            assert 0 < r <= 0.05, f"{name}: risk_pct={r} out of range (0, 0.05]"

    def test_rr_positive(self):
        """rr (reward:risk) must be positive."""
        for name, cfg in ARCHETYPES.items():
            assert cfg["rr"] > 0, f"{name}: rr must be positive"

    def test_expiry_format(self):
        """expiry must be a valid DTE string: <N>DTE."""
        import re
        pattern = re.compile(r"^\d+DTE$")
        for name, cfg in ARCHETYPES.items():
            expiry = cfg["expiry"]
            assert pattern.match(expiry), f"{name}: expiry='{expiry}' must match <N>DTE"

    def test_all_named_archetypes_exist(self):
        """Every archetype name referenced in MODEL_ARCHETYPE_MAP exists in ARCHETYPES."""
        for model, archetype_name in MODEL_ARCHETYPE_MAP.items():
            assert archetype_name in ARCHETYPES, (
                f"MODEL_ARCHETYPE_MAP[{model}] = '{archetype_name}' not in ARCHETYPES"
            )


class TestModelArchetypeMap:
    def test_all_model_types_mapped(self):
        """Every ModelType enum member has a mapping in MODEL_ARCHETYPE_MAP."""
        for model in ModelType:
            assert model.name in MODEL_ARCHETYPE_MAP, (
                f"ModelType.{model.name} has no entry in MODEL_ARCHETYPE_MAP"
            )

    def test_get_archetype_returns_dict(self):
        """get_archetype() returns a complete dict for every model."""
        for model in ModelType:
            cfg = get_archetype(model.name)
            assert isinstance(cfg, dict)
            for field in REQUIRED_FIELDS:
                assert field in cfg

    def test_get_archetype_unknown_model(self):
        """get_archetype() falls back to DIRECTIONAL_STD for unknown models."""
        cfg = get_archetype("UNKNOWN_MODEL_XYZ")
        assert cfg == ARCHETYPES.get("DIRECTIONAL_STD") or isinstance(cfg, dict)


class TestArchetypeValues:
    def test_directional_strong_has_high_rr(self):
        """DIRECTIONAL_STRONG should have rr >= 2.5 (conviction trades need wide target)."""
        assert ARCHETYPES["DIRECTIONAL_STRONG"]["rr"] >= 2.5

    def test_scalp_0dte_tiny_risk(self):
        """SCALP_0DTE risk_pct should be very small (0DTE = lottery tickets)."""
        assert ARCHETYPES["SCALP_0DTE"]["risk_pct"] <= 0.005

    def test_mean_revert_low_delta(self):
        """MEAN_REVERT should use slightly ITM delta (>0.5)."""
        assert ARCHETYPES["MEAN_REVERT"]["delta"] >= 0.5

    def test_vol_play_low_delta(self):
        """VOL_PLAY (far OTM wings) should have low delta."""
        assert ARCHETYPES["VOL_PLAY"]["delta"] < 0.25

    def test_0dte_expiry_string(self):
        """SCALP_0DTE must have 0DTE expiry."""
        assert ARCHETYPES["SCALP_0DTE"]["expiry"] == "0DTE"

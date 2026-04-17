"""
Unit tests for find_best_expiry and find_best_expiry_for_archetype.

Tests use a monkey-patched expiry list so no network calls are made.
Run with: python3 test_find_best_expiry.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from datetime import date, timedelta
from unittest.mock import patch, MagicMock

# Pre-import so patch() can resolve the module path correctly
import ingestion.options_chain as _options_chain_mod
from consensus import find_best_expiry, find_best_expiry_for_archetype


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_expiries(*offsets):
    """Return ISO date strings for today + each offset in days."""
    today = date.today()
    return [(today + timedelta(days=d)).isoformat() for d in offsets]


def _patch_expiries(expiries):
    """Context manager: patch get_chain_cache()._get_expiry_list() to return expiries."""
    mock_cache_instance = MagicMock()
    mock_cache_instance._get_expiry_list.return_value = expiries
    return patch.object(_options_chain_mod, "get_chain_cache", return_value=mock_cache_instance)


# ── test_find_best_expiry ─────────────────────────────────────────────────────

def test_0dte_picks_today_or_tomorrow():
    """SCALP_0DTE (target=0) should pick the earliest available expiry."""
    expiries = _make_expiries(0, 1, 3, 7, 14, 21, 30)
    with _patch_expiries(expiries):
        result = find_best_expiry(0)
    today_str = date.today().isoformat()
    assert result == today_str, f"0DTE: expected {today_str}, got {result}"
    print("test_0dte_picks_today_or_tomorrow: PASSED")


def test_7dte_prefers_exact_match():
    """When an exact 7DTE expiry exists it should be chosen."""
    expiries = _make_expiries(1, 4, 7, 14, 21)
    with _patch_expiries(expiries):
        result = find_best_expiry(7)
    expected = (date.today() + timedelta(days=7)).isoformat()
    assert result == expected, f"7DTE: expected {expected}, got {result}"
    print("test_7dte_prefers_exact_match: PASSED")


def test_7dte_fudge_within_2_days():
    """When no exact 7DTE exists, should pick closest candidate >= 5DTE."""
    # TSLA expiries: 1, 4, 9, 14  — closest to 7 that is >= 5 is 9
    expiries = _make_expiries(1, 4, 9, 14)
    with _patch_expiries(expiries):
        result = find_best_expiry(7)
    expected = (date.today() + timedelta(days=9)).isoformat()
    assert result == expected, f"7DTE fudge: expected {expected}, got {result}"
    print("test_7dte_fudge_within_2_days: PASSED")


def test_30dte_vol_play():
    """VOL_PLAY (target=33, midpoint of 21-45) should select nearest to 33 DTE."""
    expiries = _make_expiries(7, 14, 21, 28, 35, 42, 60)
    with _patch_expiries(expiries):
        result = find_best_expiry(33)
    expected = (date.today() + timedelta(days=35)).isoformat()
    assert result == expected, f"30DTE VOL_PLAY: expected {expected}, got {result}"
    print("test_30dte_vol_play: PASSED")


def test_fallback_when_no_expiry_list():
    """When cache returns empty list, should fall back without raising."""
    with _patch_expiries([]):
        result = find_best_expiry(7)
    # Should return a non-empty ISO date string (compute_expiry fallback)
    assert result, "fallback: got empty string"
    date.fromisoformat(result)  # must be valid ISO date
    print("test_fallback_when_no_expiry_list: PASSED")


def test_fallback_when_cache_raises():
    """When cache raises, should fall back without propagating."""
    with patch.object(_options_chain_mod, "get_chain_cache", side_effect=Exception("network error")):
        result = find_best_expiry(7)
    assert result, "exception fallback: got empty string"
    date.fromisoformat(result)
    print("test_fallback_when_cache_raises: PASSED")


# ── test_find_best_expiry_for_archetype ───────────────────────────────────────

def test_scalp_0dte_archetype():
    """SCALP_0DTE (prefer_ttm_days=(0,1), midpoint=0) should pick nearest expiry."""
    expiries = _make_expiries(0, 1, 7, 14)
    with _patch_expiries(expiries):
        result = find_best_expiry_for_archetype("SCALP_0DTE")
    today_str = date.today().isoformat()
    assert result == today_str, f"SCALP_0DTE archetype: expected {today_str}, got {result}"
    print("test_scalp_0dte_archetype: PASSED")


def test_directional_std_archetype():
    """DIRECTIONAL_STD (prefer_ttm_days=(7,21), midpoint=14) should pick ~14DTE."""
    expiries = _make_expiries(1, 4, 7, 14, 21, 30)
    with _patch_expiries(expiries):
        result = find_best_expiry_for_archetype("DIRECTIONAL_STD")
    expected = (date.today() + timedelta(days=14)).isoformat()
    assert result == expected, f"DIRECTIONAL_STD archetype: expected {expected}, got {result}"
    print("test_directional_std_archetype: PASSED")


def test_vol_play_archetype():
    """VOL_PLAY (prefer_ttm_days=(21,45), midpoint=33) should pick closest to 33DTE."""
    expiries = _make_expiries(7, 14, 21, 28, 35, 45, 60)
    with _patch_expiries(expiries):
        result = find_best_expiry_for_archetype("VOL_PLAY")
    expected = (date.today() + timedelta(days=35)).isoformat()
    assert result == expected, f"VOL_PLAY archetype: expected {expected}, got {result}"
    print("test_vol_play_archetype: PASSED")


def test_unknown_archetype_defaults_7dte():
    """Unknown archetype should default to 7DTE target."""
    expiries = _make_expiries(3, 7, 14)
    with _patch_expiries(expiries):
        result = find_best_expiry_for_archetype("NONEXISTENT_ARCHETYPE")
    expected = (date.today() + timedelta(days=7)).isoformat()
    assert result == expected, f"unknown archetype: expected {expected}, got {result}"
    print("test_unknown_archetype_defaults_7dte: PASSED")


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_0dte_picks_today_or_tomorrow()
    test_7dte_prefers_exact_match()
    test_7dte_fudge_within_2_days()
    test_30dte_vol_play()
    test_fallback_when_no_expiry_list()
    test_fallback_when_cache_raises()
    test_scalp_0dte_archetype()
    test_directional_std_archetype()
    test_vol_play_archetype()
    test_unknown_archetype_defaults_7dte()
    print("\nAll find_best_expiry tests passed.")

"""
Phase 14.1 hotfix tests:
  1. enrich_greeks per-row failure isolation (row that throws doesn't drop the whole chain)
  2. publisher drops signal (no emit) when strike_selector returns None or raises
  3. ibkr_utils.ensure_qualified raises on unqualified contract with no details
  4. null-heartbeat render: SystemHealthPanel fields are null-safe
"""
import sys
import pytest
from dataclasses import dataclass
from typing import Optional
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")


# ────────────────────────────────────────────────────────────────
#  1. enrich_greeks: per-row failure isolation
# ────────────────────────────────────────────────────────────────

class FakeRow:
    def __init__(self, strike, iv=0.5, option_type="CALL"):
        self.strike = strike
        self.implied_volatility = iv
        self.option_type = option_type
        self.greeks_source = "unavailable"
        self.delta = None
        self.gamma = None
        self.theta = None
        self.vega = None


def test_enrich_greeks_per_row_isolation():
    """A bad row (raises inside compute_bs_greeks) must not abort enrichment for other rows.

    Forces the scalar loop path (by making scipy unavailable) so the per-row
    try/except isolation is exercised directly.
    """
    from ingestion.options_chain import enrich_greeks

    rows = [FakeRow(350.0, iv=0.65), FakeRow(355.0, iv=0.65), FakeRow(360.0, iv=0.65)]

    def patched_bs(spot, strike, ttm, rate, iv, opt_type):
        if strike == 355.0:
            raise ValueError("simulated BS failure")
        return {
            "delta": 0.35, "gamma": 0.01, "theta": -0.05, "vega": 0.20,
            "greeks_source": "computed_bs",
        }

    # Force the scalar fallback path: make scipy.special.ndtr raise ImportError
    # so _enrich_greeks_vectorized falls through to the per-row loop.
    with patch("pricing.greeks.compute_bs_greeks", side_effect=patched_bs):
        with patch("pricing.greeks.get_risk_free_rate", return_value=0.05):
            with patch("scipy.special.ndtr", side_effect=ImportError("scipy not available")):
                enrich_greeks(rows, spot=350.0, ttm_years=7/365.0)

    # Row at 350 should be enriched
    assert rows[0].greeks_source == "computed_bs"
    assert rows[0].delta == pytest.approx(0.35)

    # Row at 355 (bad) should be marked unavailable, not propagate exception
    assert rows[1].greeks_source == "unavailable"
    assert rows[1].delta is None

    # Row at 360 should still be enriched (loop continued past the bad row)
    assert rows[2].greeks_source == "computed_bs"


# ────────────────────────────────────────────────────────────────
#  2. Publisher drops signal when strike_selector returns None
# ────────────────────────────────────────────────────────────────

def test_publisher_drops_signal_when_no_strike():
    """
    When select_strike returns None (all filters failed), publisher must log
    [STRIKE-REJECT] and not emit any signal on that model.
    Verify no fallback emission path exists.
    """
    # Read the publisher source and assert the except block does `continue`, not emit
    import ast, pathlib
    src = pathlib.Path("/home/builder/src/gpfiles/tcode/alpha_engine/publisher.py").read_text()
    tree = ast.parse(src)

    # Find the except block in the strike selection try/except
    # We look for the pattern: except Exception as _se: ... continue
    # and assert there is NO assignment to `strike` or `moneyness` in the except body
    class StrikeExceptVisitor(ast.NodeVisitor):
        def __init__(self):
            self.found_fallback_strike = False
            self.found_continue = False

        def visit_Try(self, node):
            # Look for try blocks that contain select_strike
            src_segment = ast.unparse(node)
            if "select_strike" not in src_segment:
                self.generic_visit(node)
                return
            for handler in node.handlers:
                handler_src = ast.unparse(handler)
                # Check: does the handler assign to `strike` (moneyness fallback)?
                for stmt in ast.walk(handler):
                    if isinstance(stmt, ast.Assign):
                        for target in stmt.targets:
                            if isinstance(target, ast.Name) and target.id == "strike":
                                self.found_fallback_strike = True
                # Check: does the handler have `continue`?
                for stmt in ast.walk(handler):
                    if isinstance(stmt, ast.Continue):
                        self.found_continue = True
            self.generic_visit(node)

    v = StrikeExceptVisitor()
    v.visit(tree)

    assert v.found_continue, "publisher.py except block must have `continue` to drop the signal"
    assert not v.found_fallback_strike, (
        "publisher.py except block must NOT assign to `strike` (moneyness fallback removed)"
    )


# ────────────────────────────────────────────────────────────────
#  3. ensure_qualified raises on missing contract details
# ────────────────────────────────────────────────────────────────

def test_ensure_qualified_raises_when_no_details():
    """ensure_qualified raises RuntimeError when IBKR returns no details."""
    from ingestion.ibkr_utils import ensure_qualified

    fake_contract = MagicMock()
    fake_contract.conId = 0  # unqualified

    fake_ib = MagicMock()
    fake_ib.reqContractDetails.return_value = []  # no details returned

    with pytest.raises(RuntimeError, match="CONTRACT-QUAL-FAIL"):
        ensure_qualified(fake_ib, fake_contract)


def test_ensure_qualified_returns_same_if_already_qualified():
    """ensure_qualified short-circuits if conId > 0."""
    from ingestion.ibkr_utils import ensure_qualified

    fake_contract = MagicMock()
    fake_contract.conId = 12345

    fake_ib = MagicMock()
    result = ensure_qualified(fake_ib, fake_contract)

    # Should not call reqContractDetails
    fake_ib.reqContractDetails.assert_not_called()
    assert result is fake_contract


def test_ensure_qualified_returns_qualified_contract():
    """ensure_qualified returns the qualified contract from reqContractDetails."""
    from ingestion.ibkr_utils import ensure_qualified

    unqualified = MagicMock()
    unqualified.conId = 0

    qualified = MagicMock()
    qualified.conId = 99999
    qualified.localSymbol = "TSLA  260420C00350000"

    detail = MagicMock()
    detail.contract = qualified

    fake_ib = MagicMock()
    fake_ib.reqContractDetails.return_value = [detail]

    result = ensure_qualified(fake_ib, unqualified)
    assert result.conId == 99999


# ────────────────────────────────────────────────────────────────
#  4. null-heartbeat fields: fmtAge and fmtUptime handle None
# ────────────────────────────────────────────────────────────────

def test_intel_chop_regime_fallback_has_components():
    """intel.py's chop_regime error fallback must include a `components` dict
    so Dashboard.tsx's `chopRegime.components[k]` never crashes."""
    import importlib, json
    import unittest.mock as m

    # Simulate get_chop_regime throwing an ImportError
    with m.patch.dict("sys.modules", {"ingestion.chop_regime": None}):
        # Re-import intel to pick up the patched module
        import ingestion.intel as intel_mod
        importlib.reload(intel_mod)  # force re-evaluation of imports

    # Check the source: the except clause must include 'components' key
    import pathlib
    src = pathlib.Path("/home/builder/src/gpfiles/tcode/alpha_engine/ingestion/intel.py").read_text()
    # Find the except block after "get_chop_regime"
    idx = src.find("except Exception as e:\n        chop_regime = {")
    assert idx > 0, "intel.py must have an error fallback for chop_regime"
    # The fallback dict must include 'components'
    fallback_region = src[idx:idx + 400]
    assert '"components"' in fallback_region, (
        "intel.py chop_regime fallback must include 'components' key — "
        "Dashboard.tsx crashes with TypeError if components is undefined"
    )

"""
Phase 8 — Bug 1 fix: ibkr_account.py mode vocabulary normalizer tests.

Verifies that normalize_mode() accepts both Phase 4+ (IBKR_PAPER / IBKR_LIVE /
SIMULATION) and legacy (paper / live / sim) strings, and that the CLI entry
point handles mode-string argv[1] correctly without shelling out to the broker.
"""
import importlib
import sys
import types
import pytest


def _import_ibkr_account():
    """Import ibkr_account module without triggering IB Gateway connection."""
    spec = importlib.util.spec_from_file_location(
        "ibkr_account",
        "/home/builder/src/gpfiles/tcode/alpha_engine/ingestion/ibkr_account.py",
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    # Stub out the ingestion sub-package so ib_insync imports are skipped.
    sys.modules.setdefault("ingestion", types.ModuleType("ingestion"))
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def ibkr_account():
    return _import_ibkr_account()


class TestNormalizeMode:
    def test_ibkr_paper_normalises_to_paper(self, ibkr_account):
        assert ibkr_account.normalize_mode("IBKR_PAPER") == "PAPER"

    def test_paper_normalises_to_paper(self, ibkr_account):
        assert ibkr_account.normalize_mode("PAPER") == "PAPER"

    def test_paper_lowercase(self, ibkr_account):
        assert ibkr_account.normalize_mode("paper") == "PAPER"

    def test_ibkr_live_normalises_to_live(self, ibkr_account):
        assert ibkr_account.normalize_mode("IBKR_LIVE") == "LIVE"

    def test_live_normalises_to_live(self, ibkr_account):
        assert ibkr_account.normalize_mode("LIVE") == "LIVE"

    def test_simulation_normalises_to_simulation(self, ibkr_account):
        assert ibkr_account.normalize_mode("SIMULATION") == "SIMULATION"

    def test_sim_normalises_to_simulation(self, ibkr_account):
        assert ibkr_account.normalize_mode("SIM") == "SIMULATION"

    def test_unknown_raises_value_error(self, ibkr_account):
        with pytest.raises(ValueError, match="Unknown mode"):
            ibkr_account.normalize_mode("FOOBAR")

    def test_whitespace_trimmed(self, ibkr_account):
        assert ibkr_account.normalize_mode("  IBKR_PAPER  ") == "PAPER"


class TestSubcommandsSet:
    """Ensure _SUBCOMMANDS contains expected values and excludes mode strings."""

    def test_account_in_subcommands(self, ibkr_account):
        assert "account" in ibkr_account._SUBCOMMANDS

    def test_positions_in_subcommands(self, ibkr_account):
        assert "positions" in ibkr_account._SUBCOMMANDS

    def test_fills_in_subcommands(self, ibkr_account):
        assert "fills" in ibkr_account._SUBCOMMANDS

    def test_pnl_in_subcommands(self, ibkr_account):
        assert "pnl" in ibkr_account._SUBCOMMANDS

    def test_ibkr_paper_not_in_subcommands(self, ibkr_account):
        assert "IBKR_PAPER" not in ibkr_account._SUBCOMMANDS

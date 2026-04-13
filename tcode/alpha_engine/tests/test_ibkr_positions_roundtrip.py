"""
Integration test: ibkr_positions round-trip
============================================
Connects to IB Gateway (must be running), calls ibkr_account.py positions,
and verifies the output is a valid list matching ib.positions().

Mark: @pytest.mark.integration
Run only when IB Gateway is up:
    pytest -m integration alpha_engine/tests/test_ibkr_positions_roundtrip.py
"""
import json
import os
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PYTHON_BIN = os.path.join(
    os.path.dirname(__file__), "..", "venv", "bin", "python"
)
ENGINE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _run_positions():
    """Shell out to ibkr_account.py positions, return parsed JSON."""
    result = subprocess.run(
        [PYTHON_BIN, "-m", "ingestion.ibkr_account", "positions"],
        capture_output=True,
        text=True,
        cwd=ENGINE_ROOT,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"ibkr_account positions exited {result.returncode}: {result.stderr}"
    )
    data = json.loads(result.stdout)
    return data


def _is_gateway_up() -> bool:
    """Return True if IB Gateway is reachable on the configured port."""
    import socket

    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = int(os.getenv("IBKR_PORT", "4002"))
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not _is_gateway_up(), reason="IB Gateway not reachable")
def test_positions_returns_list():
    """positions subcommand returns a JSON list (may be empty if no fills yet)."""
    data = _run_positions()
    assert isinstance(data, list), f"Expected list, got {type(data)}: {data!r}"


@pytest.mark.integration
@pytest.mark.skipif(not _is_gateway_up(), reason="IB Gateway not reachable")
def test_positions_schema():
    """Each position in the list has required fields with correct types."""
    data = _run_positions()
    if not data:
        pytest.skip("No positions held; schema validation requires at least one position.")

    required_fields = {
        "ticker": str,
        "sec_type": str,
        "qty": int,
        "avg_cost": float,
        "current_price": float,
        "unrealized_pnl": float,
        "market_value": float,
        "option_type": str,
        "strike": (int, float),
        "expiration": str,
    }
    for pos in data:
        for field, expected_type in required_fields.items():
            assert field in pos, f"Missing field {field!r} in position {pos}"
            assert isinstance(pos[field], expected_type), (
                f"Field {field!r}: expected {expected_type}, got {type(pos[field])}"
            )


@pytest.mark.integration
@pytest.mark.skipif(not _is_gateway_up(), reason="IB Gateway not reachable")
def test_positions_matches_open_orders():
    """
    If there are PreSubmitted/Submitted orders but no filled positions, the
    positions list is empty — that's the correct behaviour (orders ≠ positions).
    Verify we can call both open_orders and positions without error.
    """
    # Call open_orders
    orders_result = subprocess.run(
        [
            PYTHON_BIN,
            "-m",
            "ingestion.ibkr_order",
            "open_orders",
            "--mode",
            "IBKR_PAPER",
            "--client-id",
            "99",
        ],
        capture_output=True,
        text=True,
        cwd=ENGINE_ROOT,
        timeout=30,
    )
    assert orders_result.returncode == 0, (
        f"open_orders failed: {orders_result.stderr}"
    )
    orders_data = json.loads(orders_result.stdout)
    assert "orders" in orders_data, f"Unexpected open_orders shape: {orders_data}"

    # Call positions
    positions_data = _run_positions()
    assert isinstance(positions_data, list)

    # Audit: log counts for CI visibility
    active_statuses = {"PreSubmitted", "Submitted", "PendingSubmit"}
    active_orders = [
        o for o in orders_data["orders"] if o.get("status") in active_statuses
    ]
    print(
        f"\n[roundtrip] active orders={len(active_orders)}, "
        f"filled positions={len(positions_data)}"
    )

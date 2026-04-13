"""
Round-trip broker reflection test for ibkr_order.py.

Requires a live IB Gateway (paper) connection.  Only runs when:
  IBKR_GATEWAY_RUNNING=1  AND  EXECUTION_MODE=IBKR_PAPER

The test places a well-out-of-the-money order (limit price $0.01) that is
unlikely to fill immediately, verifies it appears in open_orders, then cancels
it.  This proves the order actually reached the broker — not a simulation.

Mark: @pytest.mark.integration
Include in scripts/integrity_gate.sh when IBKR_GATEWAY_RUNNING=1.
"""
import json
import os
import subprocess
import sys
import pytest

# Locate the venv python relative to this file
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_PYTHON = os.path.join(_REPO_ROOT, "alpha_engine", "venv", "bin", "python")
_ALPHA_ENGINE_DIR = os.path.join(_REPO_ROOT, "alpha_engine")


def _run(command: str, **kwargs) -> dict:
    """Run ibkr_order.py with the given subcommand string and return parsed JSON."""
    args = [_PYTHON, "-m", "ingestion.ibkr_order"] + command.split()
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        cwd=_ALPHA_ENGINE_DIR,
        env={**os.environ, "EXECUTION_MODE": "IBKR_PAPER"},
        timeout=30,
        **kwargs,
    )
    assert result.returncode == 0, (
        f"ibkr_order.py returned exit code {result.returncode}\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    return json.loads(result.stdout)


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("IBKR_GATEWAY_RUNNING") != "1",
    reason="IB Gateway not running (set IBKR_GATEWAY_RUNNING=1 to enable)",
)
@pytest.mark.skipif(
    os.getenv("EXECUTION_MODE", "IBKR_PAPER") != "IBKR_PAPER",
    reason="Only runs in IBKR_PAPER mode",
)
def test_place_order_reflects_in_broker():
    """
    Place a well-OTM test order → verify it appears in broker's open orders → cancel.
    This is the round-trip proof that ibkr_order.py reaches IBKR and is not a stub.
    """
    from datetime import date, timedelta

    # Use the next available Friday as expiry (adjust as needed)
    today = date.today()
    days_ahead = (4 - today.weekday()) % 7  # Friday = weekday 4
    if days_ahead == 0:
        days_ahead = 7
    expiry = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    # 1. Place a well-OTM order unlikely to fill immediately (limit $0.01)
    place_cmd = (
        f"place --symbol TSLA --contract CALL --strike 999 "
        f"--expiry {expiry} --action BUY --quantity 1 "
        f"--limit-price 0.01 --mode IBKR_PAPER --client-id 10"
    )
    order = _run(place_cmd)

    assert "orderId" in order, f"No orderId in response: {order}"
    assert order["orderId"] > 0, f"Invalid orderId: {order['orderId']}"
    order_id = order["orderId"]

    # 2. Independently query IBKR's open orders
    opens = _run("open_orders --mode IBKR_PAPER --client-id 11")
    assert "orders" in opens, f"open_orders response missing 'orders': {opens}"

    # 3. Our order must appear in the broker's list
    found = any(o["orderId"] == order_id for o in opens["orders"])
    assert found, (
        f"Order {order_id} did NOT appear in broker open_orders — "
        f"order did not reach IBKR. open_orders: {json.dumps(opens['orders'], indent=2)}"
    )

    # 4. Cancel the test order to clean up
    cancel = _run(f"cancel --order-id {order_id} --mode IBKR_PAPER --client-id 12")
    assert cancel.get("status") == "Cancelled", (
        f"Cancel did not return Cancelled status: {cancel}"
    )

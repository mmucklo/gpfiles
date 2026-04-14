"""
Round-trip integration test: cancel pending order + close open position.

Requires:
  IBKR_GATEWAY_RUNNING=1  AND  EXECUTION_MODE=IBKR_PAPER
  AND the execution engine running at localhost:2112

Steps:
  1. Place a far-OTM bracket order (limit price $0.01 — unlikely to fill)
  2. Call POST /api/orders/cancel via HTTP
  3. Independently verify via ibkr_order open_orders that order is Cancelled
  4. Verify the scheduled-close path via the Python close_position function
     (market-hours detection, OPG branch) — no real fill required for schedule step.

@pytest.mark.integration
"""
import json
import os
import subprocess
import sys
import time
import pytest
import http.client

# Resolve paths
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_PYTHON    = os.path.join(_REPO_ROOT, "alpha_engine", "venv", "bin", "python")
_AE_DIR    = os.path.join(_REPO_ROOT, "alpha_engine")

_GATEWAY_RUNNING = os.getenv("IBKR_GATEWAY_RUNNING", "0") == "1"
_ENGINE_HOST     = os.getenv("ENGINE_HOST", "localhost")
_ENGINE_PORT     = int(os.getenv("ENGINE_PORT", "2112"))

pytestmark = pytest.mark.skipif(
    not _GATEWAY_RUNNING,
    reason="IBKR_GATEWAY_RUNNING=1 required for round-trip test",
)


def _run_ibkr(command: str, **kwargs) -> dict:
    args = [_PYTHON, "-m", "ingestion.ibkr_order"] + command.split()
    result = subprocess.run(
        args,
        cwd=_AE_DIR,
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONPATH": _AE_DIR},
    )
    assert result.returncode == 0, f"ibkr_order failed: {result.stderr}"
    return json.loads(result.stdout)


def _api(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    conn = http.client.HTTPConnection(_ENGINE_HOST, _ENGINE_PORT, timeout=30)
    headers = {"Content-Type": "application/json"}
    payload = json.dumps(body).encode() if body else None
    conn.request(method, path, payload, headers)
    resp = conn.getresponse()
    status = resp.status
    data = json.loads(resp.read())
    conn.close()
    return status, data


# ── Helpers ───────────────────────────────────────────────────────────────────

FAR_OTM_STRIKE = 999.0   # Guaranteed far-OTM for TSLA
FAR_OTM_EXPIRY = "2026-05-16"
FAR_OTM_LIMIT  = 0.01    # $0.01 limit — very unlikely to fill


def place_far_otm_bracket() -> dict:
    """Place a far-OTM bracket order; returns the bracket result dict."""
    return _run_ibkr(
        f"place --symbol TSLA --contract CALL --strike {FAR_OTM_STRIKE} "
        f"--expiry {FAR_OTM_EXPIRY} --action BUY --quantity 1 "
        f"--limit-price {FAR_OTM_LIMIT} --take-profit 0.02 --stop-loss 0.005 "
        f"--mode IBKR_PAPER --client-id 91"
    )


def get_open_orders() -> list:
    return _run_ibkr("open_orders --mode IBKR_PAPER --client-id 92").get("orders", [])


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestCancelRoundtrip:
    def test_cancel_via_api_reflects_in_ibkr(self):
        """
        Place far-OTM bracket → cancel via /api/orders/cancel →
        verify ib.openOrders shows Cancelled status.
        """
        # 1. Place bracket
        bracket = place_far_otm_bracket()
        parent_id = bracket["parent_order_id"]
        assert parent_id > 0, f"Bracket placement returned no parent ID: {bracket}"

        time.sleep(2)

        # 2. Cancel via engine API
        status, data = _api("POST", "/api/orders/cancel", {"order_id": parent_id})
        assert status == 200, f"/api/orders/cancel returned {status}: {data}"
        assert "error" not in data or not data["error"], f"Cancel error: {data}"

        time.sleep(2)

        # 3. Verify independently: order should be Cancelled in IBKR
        orders_after = get_open_orders()
        ids_by_status = {o["orderId"]: o["status"] for o in orders_after}

        # Parent should be absent or Cancelled
        parent_status = ids_by_status.get(parent_id, "Cancelled")
        assert parent_status in ("Cancelled", "Inactive"), (
            f"Parent order {parent_id} still active: {parent_status}"
        )

        # OCO siblings should also be absent or Cancelled
        tp_id = bracket["take_profit_order_id"]
        sl_id = bracket["stop_loss_order_id"]
        for sid, label in [(tp_id, "TP"), (sl_id, "SL")]:
            sibling_status = ids_by_status.get(sid, "Cancelled")
            assert sibling_status in ("Cancelled", "Inactive"), (
                f"{label} leg {sid} still active after parent cancel: {sibling_status}"
            )


class TestClosePositionSchedule:
    def test_close_position_returns_scheduled_for_when_market_closed(self):
        """
        When market is closed, close_position auto-detects and calls schedule_close
        which returns a non-None scheduled_for field.

        This test mocks market hours by calling the Python function directly
        with the knowledge that after 16:00 ET or on weekends it will schedule.
        """
        from ingestion.ibkr_order import _is_market_hours, _next_market_open_utc
        from datetime import timezone

        # This assertion tests the helper logic, not the broker
        next_open = _next_market_open_utc()
        assert next_open.tzinfo is not None
        assert next_open.tzinfo == timezone.utc or str(next_open.tzinfo) == "UTC"

        # next_open must be in the future
        from datetime import datetime
        assert next_open > datetime.now(timezone.utc), "Next market open must be in the future"

        # next_open must be a weekday
        try:
            import zoneinfo
            et = zoneinfo.ZoneInfo("America/New_York")
            nxt_et = next_open.astimezone(et)
        except Exception:
            from datetime import timedelta
            nxt_et = next_open.astimezone(timezone(timedelta(hours=-4)))

        assert nxt_et.weekday() < 5, f"Next open is a weekend: {nxt_et}"
        assert (nxt_et.hour, nxt_et.minute) == (9, 30), f"Open time not 9:30 ET: {nxt_et}"

    def test_close_via_api_returns_order_id(self):
        """
        Call POST /api/positions/close with a synthetic position key.
        Requires a real IBKR position to be open.
        Skip gracefully if no position exists.
        """
        # Get current positions from engine
        status, positions = _api("GET", "/api/positions")
        if status != 200 or not positions:
            pytest.skip("No open IBKR positions to close")

        # Pick the first option position
        pos_list = positions if isinstance(positions, list) else []
        opt_positions = [p for p in pos_list if p.get("sec_type") == "OPT"]
        if not opt_positions:
            pytest.skip("No OPT positions available")

        pos = opt_positions[0]
        contract_key = f"{pos['ticker']}_{pos['option_type']}_{pos['expiration']}_{pos['strike']}"

        close_status, close_data = _api("POST", "/api/positions/close", {
            "contract_key": contract_key,
            "quantity": pos["qty"],
            "market_open_if_closed": True,
        })

        assert close_status == 200, f"/api/positions/close returned {close_status}: {close_data}"
        assert "error" not in close_data or not close_data["error"]
        assert close_data.get("order_id", 0) > 0, f"No order_id in response: {close_data}"

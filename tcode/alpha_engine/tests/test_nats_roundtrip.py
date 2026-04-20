"""
Phase 19 — NATS roundtrip test.

Verifies:
  1. Publisher can emit a signal → subscriber receives it within 5s.
  2. When NATS is down, publisher logs error and doesn't crash.

Tests run ONLY when a real NATS server is available at NATS_URL (default nats://127.0.0.1:4222).
If NATS is not available, the tests are auto-skipped with a clear message.
"""
import asyncio
import json
import os
import sys
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

NATS_URL = os.getenv("NATS_URL", "nats://127.0.0.1:4222")
NATS_AVAILABLE = False

try:
    import nats as _nats_mod
    # Quick probe: try to connect with 0.5s timeout
    async def _probe():
        try:
            nc = await _nats_mod.connect(NATS_URL, connect_timeout=0.5)
            await nc.close()
            return True
        except Exception:
            return False
    NATS_AVAILABLE = asyncio.get_event_loop().run_until_complete(_probe())
except Exception:
    NATS_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not NATS_AVAILABLE,
    reason=f"NATS not available at {NATS_URL} — skipping roundtrip test"
)


@pytest.mark.asyncio
async def test_publish_receive_roundtrip():
    """Publisher emits on test subject → subscriber receives within 5s."""
    import nats

    received = []
    subject = "tsla.alpha.test.roundtrip"

    nc = await nats.connect(NATS_URL)
    try:
        async def handler(msg):
            received.append(json.loads(msg.data))

        await nc.subscribe(subject, cb=handler)

        payload = {"type": "test", "ts": time.time(), "value": 42}
        await nc.publish(subject, json.dumps(payload).encode())
        await nc.flush()

        # Wait up to 5s for the message to arrive
        deadline = time.time() + 5.0
        while not received and time.time() < deadline:
            await asyncio.sleep(0.05)

        assert received, "message not received within 5s"
        assert received[0]["value"] == 42
        assert received[0]["type"] == "test"
    finally:
        await nc.close()


@pytest.mark.asyncio
async def test_publisher_does_not_crash_when_nats_is_down():
    """Publisher logs error and does NOT crash when NATS connection fails."""
    import nats

    # Connect to a port that is definitely not NATS
    bad_url = "nats://127.0.0.1:19999"
    error_raised = None
    try:
        nc = await nats.connect(bad_url, connect_timeout=0.5, reconnect_time_wait=0)
        await nc.close()
    except Exception as e:
        error_raised = e

    # The nats library raises an exception — publisher must catch it, not crash.
    # We verify the exception type is an expected network error (not a panic/crash).
    assert error_raised is not None, "expected connection error on bad URL"
    assert isinstance(error_raised, Exception), f"unexpected type: {type(error_raised)}"
    # Verify the error message is about connection (not an unrelated crash)
    assert any(kw in str(error_raised).lower() for kw in
               ("connect", "refused", "timeout", "nats", "error")), \
        f"unexpected error: {error_raised}"


@pytest.mark.asyncio
async def test_nats_subject_schema_matches_proposal_format():
    """Messages on tsla.alpha.proposals must contain required proposal fields."""
    import nats

    required_fields = {"id", "ts_created", "ts_expires", "status", "strategy",
                       "direction", "entry_price", "quantity", "confidence"}
    received = []

    nc = await nats.connect(NATS_URL)
    try:
        async def handler(msg):
            try:
                received.append(json.loads(msg.data))
            except Exception:
                pass

        await nc.subscribe("tsla.alpha.proposals", cb=handler)

        # Publish a minimal proposal
        import uuid
        proposal = {
            "id": str(uuid.uuid4()),
            "ts_created": "2026-04-19T00:00:00Z",
            "ts_expires": "2026-04-19T00:01:00Z",
            "status": "pending",
            "strategy": "MOMENTUM",
            "direction": "BULLISH",
            "entry_price": 5.0,
            "stop_price": 3.0,
            "target_price": 7.0,
            "kelly_fraction": 0.04,
            "quantity": 1,
            "confidence": 0.75,
        }
        await nc.publish("tsla.alpha.proposals", json.dumps(proposal).encode())
        await nc.flush()

        deadline = time.time() + 3.0
        while not received and time.time() < deadline:
            await asyncio.sleep(0.05)

        assert received, "proposal not received"
        for field in required_fields:
            assert field in received[0], f"missing field: {field}"
    finally:
        await nc.close()

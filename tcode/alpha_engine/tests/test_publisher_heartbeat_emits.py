"""
Integration test: start publisher.py in a subprocess for 15 seconds, then verify
≥ 2 publisher heartbeat rows were written to the DB.

This test requires a running NATS server at nats://127.0.0.1:4222.
If NATS is not available, the publisher will log a connection error but should
still write heartbeats (the NATS publish path is best-effort).

Skip this test in CI environments where NATS is unavailable by setting:
  SKIP_PUBLISHER_INTEGRATION=1

Design intent: this test proves that the heartbeat path in publisher.py
actually fires during normal operation — not just that the DB schema exists.
"""
import os
import signal
import sqlite3
import subprocess
import sys
import time

import pytest

SKIP_IF_NO_NATS = os.environ.get("SKIP_PUBLISHER_INTEGRATION") == "1"
TCODE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
VENV_PYTHON = os.path.join(TCODE_DIR, "alpha_engine/venv/bin/python")
PUBLISHER_SCRIPT = os.path.join(TCODE_DIR, "alpha_engine/publisher.py")
DB_PATH = os.path.expanduser("~/tsla_alpha.db")


def _db_exists() -> bool:
    return os.path.exists(DB_PATH)


def _count_heartbeats(component: str) -> int:
    if not _db_exists():
        return 0
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        count = conn.execute(
            "SELECT COUNT(*) FROM process_heartbeats WHERE component=?", (component,)
        ).fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 0


@pytest.mark.skipif(
    SKIP_IF_NO_NATS or not os.path.exists(VENV_PYTHON),
    reason="Publisher integration test requires venv + NATS (set SKIP_PUBLISHER_INTEGRATION=1 to skip)",
)
def test_publisher_emits_heartbeats():
    """Start publisher.py for 15s and assert ≥ 2 publisher heartbeat rows in DB."""
    before = _count_heartbeats("publisher")

    proc = subprocess.Popen(
        [VENV_PYTHON, PUBLISHER_SCRIPT],
        cwd=TCODE_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        preexec_fn=os.setsid,  # new process group so we can kill children
    )

    try:
        time.sleep(15)
    finally:
        # Send SIGTERM to the process group
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)

    after = _count_heartbeats("publisher")
    new_rows = after - before
    assert new_rows >= 2, (
        f"Expected ≥ 2 publisher heartbeat rows after 15s, got {new_rows} "
        f"(before={before}, after={after})"
    )

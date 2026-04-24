"""
pause_leak_detector.py — Phase 21: pause-leak watchdog daemon.

Monitors /tmp/pause_blocks.jsonl for blocked-call records written by
pause_guard.py. When the system is paused, this file should be empty
(or have only very old entries). If NEW entries appear while the system
claims to be paused, it indicates a leak — an ingestion function is
making external calls despite the guard.

AlertThrottle: first alert fires immediately; subsequent alerts for the
same function within window_sec (600s = 10 min) are batched into a
single digest, preventing alert fatigue.

Writes /tmp/pause_watchdog_status.json every poll cycle so the frontend
badge and Go API endpoint can read it without spawning a subprocess.

Usage (run as a background daemon):
    python3 pause_leak_detector.py

Environment:
    PAUSE_BLOCKS_LOG      path to pause_blocks.jsonl (default /tmp/pause_blocks.jsonl)
    WATCHDOG_STATUS_FILE  path for status JSON (default /tmp/pause_watchdog_status.json)
    WATCHDOG_POLL_SEC     poll interval in seconds (default 30)
    TELEGRAM_TOKEN        bot token for Telegram alerts (optional)
    TELEGRAM_CHAT_ID      chat ID for Telegram alerts (optional)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WATCHDOG] %(levelname)s %(message)s",
)
logger = logging.getLogger("PauseWatchdog")

# ── Configuration ──────────────────────────────────────────────────────────────

PAUSE_BLOCKS_LOG: str = os.getenv("PAUSE_BLOCKS_LOG", "/tmp/pause_blocks.jsonl")
WATCHDOG_STATUS_FILE: str = os.getenv("WATCHDOG_STATUS_FILE", "/tmp/pause_watchdog_status.json")
WATCHDOG_POLL_SEC: float = float(os.getenv("WATCHDOG_POLL_SEC", "30"))


# ── Alert throttling ───────────────────────────────────────────────────────────

class AlertThrottle:
    """Throttle repeated alerts for the same key within a rolling window.

    First alert per key fires immediately. Subsequent alerts within window_sec
    are batched; a digest is sent once when the window closes.
    """

    def __init__(self, window_sec: float = 600.0) -> None:
        self.window_sec = window_sec
        self._first_seen: dict[str, float] = {}   # key → first alert epoch
        self._batch: dict[str, list[dict]] = defaultdict(list)  # key → buffered records
        self._last_digest_sent: dict[str, float] = {}

    def record(self, key: str, record: dict) -> bool:
        """Record an event. Returns True if an immediate alert should fire.

        After the first alert, events are batched until drain() is called.
        """
        now = time.time()
        if key not in self._first_seen:
            self._first_seen[key] = now
            self._last_digest_sent[key] = now
            return True  # fire immediately
        self._batch[key].append(record)
        return False

    def drain(self) -> dict[str, list[dict]]:
        """Return batched records for keys whose window has expired. Clears batch."""
        now = time.time()
        digests: dict[str, list[dict]] = {}
        for key, records in list(self._batch.items()):
            if records and now - self._last_digest_sent.get(key, 0) >= self.window_sec:
                digests[key] = records
                self._batch[key] = []
                self._last_digest_sent[key] = now
        return digests


# ── Telegram helper ────────────────────────────────────────────────────────────

def _send_telegram(msg: str) -> None:
    """Send a Telegram message if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID are set."""
    token = os.getenv("TELEGRAM_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        import urllib.request
        payload = json.dumps({"chat_id": chat_id, "text": msg}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as exc:
        logger.warning("Telegram alert failed: %s", exc)


# ── Watchdog state ─────────────────────────────────────────────────────────────

def _write_status(leaks: list[dict], last_checked: float, paused: bool) -> None:
    """Write /tmp/pause_watchdog_status.json for the Go API + frontend badge."""
    status = {
        "ok": len(leaks) == 0,
        "paused": paused,
        "leak_count": len(leaks),
        "leaks": leaks[-10:],  # last 10 leak records for display
        "last_checked": last_checked,
    }
    try:
        tmp = WATCHDOG_STATUS_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(status, fh)
        os.replace(tmp, WATCHDOG_STATUS_FILE)
    except OSError as exc:
        logger.warning("Failed to write watchdog status: %s", exc)


# ── Import pause check ─────────────────────────────────────────────────────────

_ALPHA_ENGINE = os.path.dirname(os.path.abspath(__file__))
if _ALPHA_ENGINE not in sys.path:
    sys.path.insert(0, _ALPHA_ENGINE)


def _is_currently_paused() -> bool:
    try:
        from pause_guard import _read_raw
        return _read_raw()
    except Exception:
        return False


# ── Main loop ──────────────────────────────────────────────────────────────────

def run_watchdog() -> None:
    """Blocking watchdog loop. Run as a daemon process."""
    logger.info("Pause leak watchdog started (poll=%.0fs, log=%s)", WATCHDOG_POLL_SEC, PAUSE_BLOCKS_LOG)
    throttle = AlertThrottle(window_sec=600.0)
    _last_offset: int = 0          # byte offset to track new JSONL lines
    _active_leaks: list[dict] = []

    while True:
        paused = _is_currently_paused()
        new_leaks: list[dict] = []

        if paused:
            # Read any new entries from PAUSE_BLOCKS_LOG
            try:
                path = Path(PAUSE_BLOCKS_LOG)
                if path.exists():
                    with open(path, "r") as fh:
                        fh.seek(_last_offset)
                        for line in fh:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                record = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            # Only flag records written AFTER the last poll
                            if record.get("ts", 0) > time.time() - WATCHDOG_POLL_SEC * 2:
                                new_leaks.append(record)
                        _last_offset = fh.tell()
            except OSError:
                pass

            for record in new_leaks:
                fn_key = f"{record.get('module', '?')}.{record.get('fn', '?')}"
                _active_leaks.append(record)
                if throttle.record(fn_key, record):
                    msg = (
                        f"🚨 PAUSE LEAK: {fn_key} made an external call "
                        f"while system is PAUSED at {time.strftime('%H:%M:%S UTC', time.gmtime(record.get('ts', time.time())))}"
                    )
                    logger.error(msg)
                    _send_telegram(msg)

            # Send digest for throttled alerts
            for key, records in throttle.drain().items():
                msg = (
                    f"⚠️ PAUSE LEAK DIGEST: {key} was called {len(records)} more time(s) "
                    f"while paused in the last 10 minutes."
                )
                logger.warning(msg)
                _send_telegram(msg)

        else:
            # System unpaused — clear active leaks list
            if _active_leaks:
                logger.info("System unpaused; clearing %d active leak records", len(_active_leaks))
                _active_leaks.clear()

        _write_status(
            leaks=_active_leaks,
            last_checked=time.time(),
            paused=paused,
        )

        time.sleep(WATCHDOG_POLL_SEC)


if __name__ == "__main__":
    run_watchdog()

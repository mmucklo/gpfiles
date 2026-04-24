"""
pause_guard.py — Phase 21: bulletproof pause-means-silent guarantee.

Provides:
  is_paused()              — reads pause state with 500ms TTL cache
  @pause_guard             — decorator for sync ingestion entry points
  @pause_guard_async       — decorator for async ingestion entry points
  _record_blocked_call()   — appends a JSONL entry to PAUSE_BLOCKS_LOG

When paused, decorated functions return None immediately (no network I/O).
Every blocked call is logged to stderr and recorded in PAUSE_BLOCKS_LOG
so the watchdog daemon can detect leaks.

Pause state is read from PAUSE_STATE_FILE (same file the Go API writes).
Default: /tmp/tsla_alpha_pause_state.json (overridable via env var).
"""
from __future__ import annotations

import functools
import json
import logging
import os
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

PAUSE_STATE_FILE: str = os.getenv(
    "PUBLISHER_PAUSE_STATE_FILE",
    "/tmp/tsla_alpha_pause_state.json",
)
PAUSE_BLOCKS_LOG: str = os.getenv(
    "PAUSE_BLOCKS_LOG",
    "/tmp/pause_blocks.jsonl",
)

# TTL for the in-process pause-state cache (seconds).
_CACHE_TTL_SEC: float = 0.5

# ── Cache ──────────────────────────────────────────────────────────────────────

_cache_value: bool = True          # safe default: treat as paused
_cache_expires_at: float = 0.0     # epoch seconds


def _read_raw() -> bool:
    """Read pause state from disk. Returns True (paused) on any error."""
    import datetime as _dt
    try:
        with open(PAUSE_STATE_FILE) as fh:
            state = json.load(fh)
        if state.get("paused", True):
            return True
        # Check expiry
        until_str = state.get("unpause_until")
        if until_str:
            until = _dt.datetime.fromisoformat(until_str.replace("Z", "+00:00"))
            if _dt.datetime.now(_dt.timezone.utc) > until:
                return True   # window expired → paused
        return False
    except Exception:
        return True   # any error → fail-safe: paused


def is_paused() -> bool:
    """Return True if the system is currently paused.

    Result is cached for _CACHE_TTL_SEC (500ms) to avoid disk thrash when
    many ingestion functions check in quick succession.
    """
    global _cache_value, _cache_expires_at
    now = time.monotonic()
    if now < _cache_expires_at:
        return _cache_value
    _cache_value = _read_raw()
    _cache_expires_at = now + _CACHE_TTL_SEC
    return _cache_value


# ── Block recorder ─────────────────────────────────────────────────────────────

def _record_blocked_call(fn_name: str, module: str) -> None:
    """Append a JSONL record to PAUSE_BLOCKS_LOG and emit a log warning."""
    record = {
        "ts": time.time(),
        "fn": fn_name,
        "module": module,
    }
    logger.warning("[PAUSE-GUARD] blocked %s.%s (system is paused)", module, fn_name)
    try:
        with open(PAUSE_BLOCKS_LOG, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError as exc:
        logger.debug("[PAUSE-GUARD] could not write to %s: %s", PAUSE_BLOCKS_LOG, exc)


# ── Decorators ─────────────────────────────────────────────────────────────────

def pause_guard(fn: Callable) -> Callable:
    """Decorator for *synchronous* ingestion entry points.

    When the system is paused, the function body is skipped and None is returned.
    Use on any function that makes external HTTP/socket calls.

    Usage::
        @pause_guard
        def fetch_senate_trades() -> list[dict]:
            ...
    """
    mod = fn.__module__ or "unknown"

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if is_paused():
            _record_blocked_call(fn.__name__, mod)
            return None
        return fn(*args, **kwargs)

    return wrapper


def pause_guard_async(fn: Callable) -> Callable:
    """Decorator for *async* ingestion entry points.

    Same semantics as @pause_guard but for coroutine functions.

    Usage::
        @pause_guard_async
        async def fetch_realtime_data() -> dict:
            ...
    """
    mod = fn.__module__ or "unknown"

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        if is_paused():
            _record_blocked_call(fn.__name__, mod)
            return None
        return await fn(*args, **kwargs)

    return wrapper

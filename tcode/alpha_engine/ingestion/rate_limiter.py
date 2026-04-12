"""
Phase 8: Production Rate Limiter
Thread-safe sliding window rate limiter with per-source circuit breakers.
"""
import time
import threading
import logging
from collections import deque
from typing import Optional

logger = logging.getLogger("RateLimiter")

# Default limits: (max_calls, window_seconds)
_SOURCE_LIMITS: dict[str, tuple[int, int]] = {
    "yfinance": (60, 60),
    "fred": (5, 60),
    "sec_edgar": (1, 10),
}

_CIRCUIT_BREAK_FAILURES = 5      # consecutive failures before opening circuit
_CIRCUIT_BREAK_COOLDOWN = 300    # seconds to wait before retrying (5 minutes)


class RateLimiter:
    """
    Thread-safe sliding window rate limiter with circuit breaker per source.

    Usage:
        rl = get_rate_limiter()
        if rl.check("yfinance"):
            data = yf.Ticker("TSLA").history(...)
        else:
            logger.warning("Rate limited — skipping yfinance call")
    """

    def __init__(self):
        self._lock = threading.Lock()
        # source -> deque of call timestamps within the window
        self._windows: dict[str, deque] = {}
        # Circuit breaker state per source
        self._consecutive_failures: dict[str, int] = {}
        self._circuit_open_at: dict[str, float] = {}  # timestamp when circuit opened

    def check(self, source: str) -> bool:
        """
        Returns True if a call to `source` is allowed right now.
        Respects both the rate limit and circuit breaker state.
        Does NOT record the call — call record_success() or record_failure() after.
        """
        limit, window = _SOURCE_LIMITS.get(source, (60, 60))
        now = time.time()

        with self._lock:
            # Circuit breaker check
            opened_at = self._circuit_open_at.get(source, 0.0)
            if opened_at > 0:
                if now - opened_at < _CIRCUIT_BREAK_COOLDOWN:
                    logger.warning(
                        f"[{source}] Circuit OPEN — cooling down "
                        f"({int(_CIRCUIT_BREAK_COOLDOWN - (now - opened_at))}s remaining)"
                    )
                    return False
                else:
                    # Cooldown expired — half-open: allow one probe
                    logger.info(f"[{source}] Circuit half-open — allowing probe")
                    self._circuit_open_at[source] = 0.0
                    self._consecutive_failures[source] = 0

            # Sliding window check
            q = self._windows.setdefault(source, deque())
            # Evict timestamps outside the window
            while q and now - q[0] > window:
                q.popleft()

            if len(q) >= limit:
                oldest = q[0]
                wait = window - (now - oldest)
                logger.warning(
                    f"[{source}] Rate limit reached ({limit}/{window}s) — "
                    f"retry in {wait:.1f}s"
                )
                return False

            # Record this call attempt
            q.append(now)
            return True

    def record_failure(self, source: str) -> None:
        """Call after a failed API request to track circuit breaker state."""
        with self._lock:
            count = self._consecutive_failures.get(source, 0) + 1
            self._consecutive_failures[source] = count
            if count >= _CIRCUIT_BREAK_FAILURES:
                if self._circuit_open_at.get(source, 0.0) == 0.0:
                    logger.error(
                        f"[{source}] Circuit OPENED after {count} consecutive failures"
                    )
                    self._circuit_open_at[source] = time.time()

    def record_success(self, source: str) -> None:
        """Call after a successful API request to reset failure counter."""
        with self._lock:
            self._consecutive_failures[source] = 0
            self._circuit_open_at[source] = 0.0

    def get_status(self) -> dict:
        """Return current state of all tracked sources."""
        now = time.time()
        status = {}
        with self._lock:
            all_sources = set(_SOURCE_LIMITS) | set(self._windows)
            for source in all_sources:
                limit, window = _SOURCE_LIMITS.get(source, (60, 60))
                q = self._windows.get(source, deque())
                # Count calls in current window
                recent = sum(1 for ts in q if now - ts <= window)
                opened_at = self._circuit_open_at.get(source, 0.0)
                circuit_state = "CLOSED"
                cooldown_remaining = 0
                if opened_at > 0:
                    elapsed = now - opened_at
                    if elapsed < _CIRCUIT_BREAK_COOLDOWN:
                        circuit_state = "OPEN"
                        cooldown_remaining = int(_CIRCUIT_BREAK_COOLDOWN - elapsed)
                    else:
                        circuit_state = "HALF_OPEN"
                status[source] = {
                    "calls_in_window": recent,
                    "limit": limit,
                    "window_seconds": window,
                    "consecutive_failures": self._consecutive_failures.get(source, 0),
                    "circuit": circuit_state,
                    "cooldown_remaining_s": cooldown_remaining,
                }
        return status


# Module-level singleton
_rate_limiter: Optional[RateLimiter] = None
_rl_lock = threading.Lock()


def get_rate_limiter() -> RateLimiter:
    """Return the process-wide RateLimiter singleton."""
    global _rate_limiter
    if _rate_limiter is None:
        with _rl_lock:
            if _rate_limiter is None:
                _rate_limiter = RateLimiter()
    return _rate_limiter


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    rl = get_rate_limiter()
    print("Initial status:", json.dumps(rl.get_status(), indent=2))

    # Simulate 3 calls to yfinance
    for i in range(3):
        allowed = rl.check("yfinance")
        print(f"yfinance call {i+1}: {'allowed' if allowed else 'blocked'}")
        if allowed:
            rl.record_success("yfinance")

    print("\nAfter 3 calls:", json.dumps(rl.get_status(), indent=2))

    # Simulate failures to trip circuit breaker
    for i in range(6):
        rl.check("fred")
        rl.record_failure("fred")
    print("\nAfter 6 fred failures:", json.dumps(rl.get_status(), indent=2))

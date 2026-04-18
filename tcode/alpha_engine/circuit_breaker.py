"""
Phase 17 — Daily P&L Circuit Breaker.

Rules:
  daily_pnl <= -DAILY_LOSS_LIMIT  → HARD_STOP   (publisher paused, big red banner)
  consecutive_losses >= 3          → SOFT_PAUSE  (30-min cool-off)
  daily_pnl >= DAILY_TARGET        → TARGET_REACHED (green banner — user decides)

API: GET /api/circuit-breaker
  → {status, daily_pnl, consecutive_losses, remaining_pause_sec}

The circuit breaker cannot be disabled. HARD_STOP means trading stops. Period.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("CircuitBreaker")

DB_PATH = os.path.expanduser("~/tsla_alpha.db")

# ── Config ─────────────────────────────────────────────────────────────────
DAILY_LOSS_LIMIT: float = float(os.getenv("DAILY_LOSS_LIMIT", "2500"))   # positive number
DAILY_TARGET: float     = float(os.getenv("DAILY_TARGET", "10000"))
CONSECUTIVE_LOSS_LIMIT: int  = int(os.getenv("CONSECUTIVE_LOSS_LIMIT", "3"))
COOL_OFF_MINUTES: int        = int(os.getenv("CONSECUTIVE_LOSS_PAUSE_MIN", "30"))

# Status constants
STATUS_ACTIVE          = "active"
STATUS_SOFT_PAUSE      = "soft_pause"
STATUS_HARD_STOP       = "hard_stop"
STATUS_TARGET_REACHED  = "target_reached"

# ── Runtime state ──────────────────────────────────────────────────────────
_soft_pause_until: datetime | None = None  # set when soft pause fires
_hard_stop: bool = False                   # set permanently when daily loss limit hit


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ── Trade statistics ────────────────────────────────────────────────────────

def _today_trades() -> list[dict]:
    """Fetch closed trade_ledger rows for today ordered by ts_entry."""
    today = _today_iso()
    try:
        conn = _connect()
        rows = conn.execute("""
            SELECT id, net_pnl, ts_entry
            FROM trade_ledger
            WHERE date(ts_entry) = ? AND ts_exit IS NOT NULL
            ORDER BY ts_entry ASC
        """, (today,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.debug("circuit_breaker: DB read failed: %s", exc)
        return []


def compute_daily_stats() -> dict:
    """Return daily P&L, loss count, consecutive losses, and win count."""
    trades = _today_trades()
    daily_pnl = sum(t["net_pnl"] or 0 for t in trades)
    winners = sum(1 for t in trades if (t["net_pnl"] or 0) > 0)
    losers  = sum(1 for t in trades if (t["net_pnl"] or 0) < 0)

    # Count trailing consecutive losses from the end
    consecutive = 0
    for t in reversed(trades):
        if (t["net_pnl"] or 0) < 0:
            consecutive += 1
        else:
            break

    return {
        "daily_pnl": round(daily_pnl, 2),
        "winners": winners,
        "losers": losers,
        "consecutive_losses": consecutive,
        "total_trades": len(trades),
    }


# ── State machine ────────────────────────────────────────────────────────────

def evaluate() -> dict:
    """Evaluate circuit breaker state. Fires pause side-effects if needed.

    Returns:
      {status, daily_pnl, consecutive_losses, remaining_pause_sec, ...}
    """
    global _hard_stop, _soft_pause_until

    stats = compute_daily_stats()
    daily_pnl        = stats["daily_pnl"]
    consecutive      = stats["consecutive_losses"]
    now              = datetime.now(timezone.utc)

    # 1. Hard stop (permanent for the day)
    if _hard_stop or daily_pnl <= -abs(DAILY_LOSS_LIMIT):
        if not _hard_stop:
            _hard_stop = True
            logger.warning(
                "CIRCUIT BREAKER: HARD STOP — daily P&L %.2f <= -%.0f",
                daily_pnl, DAILY_LOSS_LIMIT
            )
            _trigger_hard_stop()
        remaining = int((datetime.now(timezone.utc).replace(hour=23, minute=59, second=59) - now).total_seconds())
        return {
            "status": STATUS_HARD_STOP,
            "daily_pnl": daily_pnl,
            "consecutive_losses": consecutive,
            "remaining_pause_sec": max(0, remaining),
            **stats,
        }

    # 2. Soft pause (consecutive losses)
    if consecutive >= CONSECUTIVE_LOSS_LIMIT:
        if _soft_pause_until is None or now > _soft_pause_until:
            # Trigger new cool-off window
            _soft_pause_until = now + timedelta(minutes=COOL_OFF_MINUTES)
            logger.warning(
                "CIRCUIT BREAKER: SOFT PAUSE — %d consecutive losses; cooling off until %s",
                consecutive, _soft_pause_until.isoformat()
            )
            _trigger_soft_pause(_soft_pause_until)

    if _soft_pause_until is not None and now < _soft_pause_until:
        remaining_pause = int((_soft_pause_until - now).total_seconds())
        return {
            "status": STATUS_SOFT_PAUSE,
            "daily_pnl": daily_pnl,
            "consecutive_losses": consecutive,
            "remaining_pause_sec": remaining_pause,
            "resume_at": _soft_pause_until.isoformat().replace("+00:00", "Z"),
            **stats,
        }
    else:
        _soft_pause_until = None  # cool-off expired

    # 3. Target reached (celebration, not a stop)
    if daily_pnl >= DAILY_TARGET:
        return {
            "status": STATUS_TARGET_REACHED,
            "daily_pnl": daily_pnl,
            "consecutive_losses": consecutive,
            "remaining_pause_sec": 0,
            **stats,
        }

    # 4. Active — all clear
    return {
        "status": STATUS_ACTIVE,
        "daily_pnl": daily_pnl,
        "consecutive_losses": consecutive,
        "remaining_pause_sec": 0,
        **stats,
    }


def is_trading_blocked() -> bool:
    """Quick check: should new trades be blocked right now?"""
    state = evaluate()
    return state["status"] in (STATUS_HARD_STOP, STATUS_SOFT_PAUSE)


# ── Side-effects ─────────────────────────────────────────────────────────────

def _trigger_hard_stop() -> None:
    """Write pause state file + write system_alerts row."""
    try:
        import json
        pause_file = os.getenv("PUBLISHER_PAUSE_STATE_FILE", "/tmp/tsla_alpha_pause_state.json")
        with open(pause_file, "w") as f:
            json.dump({"paused": True, "unpause_until": None, "reason": "CIRCUIT_BREAKER_HARD_STOP"}, f)
    except Exception as exc:
        logger.error("Hard stop: failed to write pause state: %s", exc)
    _write_alert("circuit_breaker", "error",
                 f"CIRCUIT BREAKER: daily loss limit hit (limit=${DAILY_LOSS_LIMIT:.0f}). Trading paused for the day.")


def _trigger_soft_pause(until: datetime) -> None:
    """Write timed unpause state and alert."""
    try:
        import json
        pause_file = os.getenv("PUBLISHER_PAUSE_STATE_FILE", "/tmp/tsla_alpha_pause_state.json")
        with open(pause_file, "w") as f:
            json.dump({
                "paused": True,
                "unpause_until": until.isoformat().replace("+00:00", "Z"),
                "reason": "CIRCUIT_BREAKER_CONSECUTIVE_LOSSES",
            }, f)
    except Exception as exc:
        logger.error("Soft pause: failed to write pause state: %s", exc)
    until_str = until.strftime("%H:%M UTC")
    _write_alert("circuit_breaker", "degraded",
                 f"3 consecutive losses — cooling off until {until_str}")


def _write_alert(component: str, status: str, message: str) -> None:
    try:
        conn = _connect()
        conn.execute("""
            INSERT INTO system_alerts (ts, component, status, message)
            VALUES (?, ?, ?, ?)
        """, (datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), component, status, message))
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.debug("circuit_breaker: alert write failed: %s", exc)


def reset_for_new_day() -> None:
    """Call at market open to clear the hard stop flag for a new trading day."""
    global _hard_stop, _soft_pause_until
    _hard_stop = False
    _soft_pause_until = None
    logger.info("circuit_breaker: state reset for new day")

"""
Phase 17 — ATR-based Stop Manager.

Tracks open managed positions and checks stop/target/time logic every bar.

Stop priority (checked in order):
  1. Time stop (hard deadline — always fires first)
  2. Target     (take profit)
  3. Trailing   (protect gains; never moves backward)
  4. Initial    (cut loss)

Per-strategy config:
  MOMENTUM    stop=1.5× ATR  target=2.0× ATR  max_hold=15 min
  IRON_CONDOR stop=N/A (defined risk)  target=premium×0.5  max_hold=60 min
  WAVE_RIDER  stop=1.0× ATR  target=1.5× ATR  max_hold=10 min
  JADE_LIZARD stop=N/A (defined risk)  target=premium×0.5  max_hold=45 min
  STRADDLE    stop=2.0× ATR  target=3.0× ATR  max_hold=30 min
  GAMMA_SCALP stop=1.5× ATR  target=N/A (continuous) max_hold=120 min

Anti-patterns:
  - We do NOT place stops as broker orders (bad fill quality on options).
  - StopManager places a market close order when a level is breached.
  - Max hold: 120 min absolute cap. No position survives intraday.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("StopManager")

DB_PATH = os.path.expanduser("~/tsla_alpha.db")

# ── Strategy configuration ────────────────────────────────────────────────────
_MAX_HOLD_MINUTES_CAP = 120  # absolute max — no position survives past this

_STRATEGY_CONFIG: dict[str, dict] = {
    "MOMENTUM":    {"stop_mult": 1.5,  "target_mult": 2.0,  "max_hold": 15,  "defined_risk": False},
    "IRON_CONDOR": {"stop_mult": None, "target_mult": 0.5,  "max_hold": 60,  "defined_risk": True},
    "WAVE_RIDER":  {"stop_mult": 1.0,  "target_mult": 1.5,  "max_hold": 10,  "defined_risk": False},
    "JADE_LIZARD": {"stop_mult": None, "target_mult": 0.5,  "max_hold": 45,  "defined_risk": True},
    "STRADDLE":    {"stop_mult": 2.0,  "target_mult": 3.0,  "max_hold": 30,  "defined_risk": False},
    "GAMMA_SCALP": {"stop_mult": 1.5,  "target_mult": None, "max_hold": 120, "defined_risk": False},
}
_DEFAULT_CONFIG = {"stop_mult": 1.5, "target_mult": 2.0, "max_hold": 15, "defined_risk": False}

# ── Env overrides ─────────────────────────────────────────────────────────────
_DEFAULT_STOP_MULT   = float(os.getenv("DEFAULT_STOP_MULTIPLIER", "1.5"))
_DEFAULT_TARGET_MULT = float(os.getenv("DEFAULT_TARGET_MULTIPLIER", "2.0"))
_DEFAULT_MAX_HOLD    = min(int(os.getenv("MAX_HOLD_MINUTES", "15")), _MAX_HOLD_MINUTES_CAP)

# Trailing stop activation: once gain > ATR × TRAIL_ACTIVATE_MULT, engage trail
_TRAIL_ACTIVATE_MULT = 0.5  # 0.5 × ATR unrealized gain triggers trail
_TRAIL_DISTANCE_MULT = 0.75 # trail at current_price - ATR × 0.75


@dataclass
class ManagedPosition:
    trade_id: int
    entry_price: float
    entry_time: datetime
    quantity: int
    direction: str           # "LONG" | "SHORT"
    strategy: str
    # Stop levels
    initial_stop: float
    current_stop: float      # trailing stop — initialized to initial_stop
    target: Optional[float]  # None for strategies with no fixed target (GAMMA_SCALP)
    time_stop_at: datetime
    stop_multiplier: float
    target_multiplier: Optional[float]
    premium_received: Optional[float] = None   # for defined-risk strategies
    # Runtime state
    trailing_engaged: bool = False
    is_open: bool = True
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    stop_type: Optional[str] = None  # TP | SL | TIME_STOP | TRAILING | MANUAL

    @property
    def unrealized_pnl(self) -> float:
        return 0.0  # caller must supply current_price

    def unrealized(self, current_price: float) -> float:
        """Unrealized P&L in dollars (per-option dollar value)."""
        if self.direction == "LONG":
            return (current_price - self.entry_price) * self.quantity * 100
        else:
            return (self.entry_price - current_price) * self.quantity * 100


# ── In-memory position store ──────────────────────────────────────────────────
_positions: dict[int, ManagedPosition] = {}   # trade_id → ManagedPosition


def _strategy_config(strategy: str) -> dict:
    cfg = _STRATEGY_CONFIG.get(strategy.upper(), {})
    base = dict(_DEFAULT_CONFIG)
    base.update(cfg)
    # Apply env overrides for default values only when strategy is not overriding
    if strategy.upper() not in _STRATEGY_CONFIG:
        base["stop_mult"] = _DEFAULT_STOP_MULT
        base["target_mult"] = _DEFAULT_TARGET_MULT
        base["max_hold"] = _DEFAULT_MAX_HOLD
    return base


def _compute_initial_stop(entry: float, atr: float, mult: float | None, direction: str) -> float:
    if mult is None or atr == 0:
        # defined-risk: stop is far OTM, treat as 0 (position already bounded)
        return 0.0
    if direction == "LONG":
        return entry - (atr * mult)
    else:
        return entry + (atr * mult)


def _compute_target(
    entry: float,
    atr: float,
    target_mult: float | None,
    direction: str,
    premium_received: float | None = None,
    defined_risk: bool = False,
) -> float | None:
    if target_mult is None:
        return None  # GAMMA_SCALP — no fixed target
    if defined_risk and premium_received is not None:
        # Defined-risk: target = premium × 0.5 (i.e. close at 50% profit)
        return round(entry - (premium_received * target_mult), 4)
    if atr == 0:
        return None
    if direction == "LONG":
        return entry + (atr * target_mult)
    else:
        return entry - (atr * target_mult)


# ── Public API ────────────────────────────────────────────────────────────────

def open_position(
    trade_id: int,
    entry_price: float,
    quantity: int,
    direction: str,
    strategy: str,
    atr_at_entry: float,
    premium_received: float | None = None,
) -> ManagedPosition:
    """Register a new managed position. Call after trade is executed."""
    cfg = _strategy_config(strategy)
    now = datetime.now(timezone.utc)
    max_hold = min(cfg["max_hold"], _MAX_HOLD_MINUTES_CAP)

    initial_stop = _compute_initial_stop(
        entry_price, atr_at_entry, cfg["stop_mult"], direction
    )
    target = _compute_target(
        entry_price, atr_at_entry, cfg["target_mult"], direction,
        premium_received, cfg["defined_risk"]
    )

    pos = ManagedPosition(
        trade_id=trade_id,
        entry_price=entry_price,
        entry_time=now,
        quantity=quantity,
        direction=direction,
        strategy=strategy,
        initial_stop=initial_stop,
        current_stop=initial_stop,
        target=target,
        time_stop_at=now + timedelta(minutes=max_hold),
        stop_multiplier=cfg["stop_mult"] or _DEFAULT_STOP_MULT,
        target_multiplier=cfg["target_mult"],
        premium_received=premium_received,
    )
    _positions[trade_id] = pos
    logger.info(
        "StopManager: opened trade_id=%d dir=%s entry=%.4f stop=%.4f target=%s time_stop=%s",
        trade_id, direction, entry_price,
        initial_stop, f"{target:.4f}" if target else "N/A",
        pos.time_stop_at.isoformat()
    )
    return pos


def update_stops(trade_id: int, current_price: float, current_atr: float) -> tuple[bool, str]:
    """Update trailing stop and check all exit conditions.

    Returns (should_close, stop_type).
    stop_type: "TIME_STOP" | "TP" | "TRAILING" | "SL" | "" (no exit yet)
    """
    pos = _positions.get(trade_id)
    if pos is None or not pos.is_open:
        return False, ""

    now = datetime.now(timezone.utc)

    # 1. Time stop — hard deadline
    if now >= pos.time_stop_at:
        logger.info("StopManager: trade_id=%d TIME_STOP fired (held past %s)", trade_id, pos.time_stop_at)
        return True, "TIME_STOP"

    # 2. Target (take profit)
    if pos.target is not None:
        if pos.direction == "LONG" and current_price >= pos.target:
            logger.info("StopManager: trade_id=%d TP fired at %.4f (target=%.4f)", trade_id, current_price, pos.target)
            return True, "TP"
        if pos.direction == "SHORT" and current_price <= pos.target:
            logger.info("StopManager: trade_id=%d TP fired at %.4f (target=%.4f)", trade_id, current_price, pos.target)
            return True, "TP"

    # 3. Trailing stop update + check
    unrealized = pos.unrealized(current_price)
    atr_dollar = current_atr * 100 * pos.quantity  # in dollar terms
    trail_activation_threshold = _TRAIL_ACTIVATE_MULT * current_atr if current_atr > 0 else 0

    if current_atr > 0 and unrealized / (pos.quantity * 100) > trail_activation_threshold:
        # Activate/update trailing stop
        if pos.direction == "LONG":
            new_trail = current_price - (current_atr * _TRAIL_DISTANCE_MULT)
            if new_trail > pos.current_stop:  # never move backward
                pos.current_stop = new_trail
                pos.trailing_engaged = True
        else:
            new_trail = current_price + (current_atr * _TRAIL_DISTANCE_MULT)
            if new_trail < pos.current_stop:  # never move backward (shorts: lower = tighter)
                pos.current_stop = new_trail
                pos.trailing_engaged = True

    if pos.trailing_engaged:
        if pos.direction == "LONG" and current_price <= pos.current_stop:
            logger.info("StopManager: trade_id=%d TRAILING fired at %.4f (stop=%.4f)", trade_id, current_price, pos.current_stop)
            return True, "TRAILING"
        if pos.direction == "SHORT" and current_price >= pos.current_stop:
            logger.info("StopManager: trade_id=%d TRAILING fired at %.4f (stop=%.4f)", trade_id, current_price, pos.current_stop)
            return True, "TRAILING"

    # 4. Initial stop (cut loss)
    if pos.initial_stop > 0:
        if pos.direction == "LONG" and current_price <= pos.initial_stop:
            logger.info("StopManager: trade_id=%d SL fired at %.4f (stop=%.4f)", trade_id, current_price, pos.initial_stop)
            return True, "SL"
        if pos.direction == "SHORT" and current_price >= pos.initial_stop:
            logger.info("StopManager: trade_id=%d SL fired at %.4f (stop=%.4f)", trade_id, current_price, pos.initial_stop)
            return True, "SL"

    return False, ""


def close_position(trade_id: int, exit_price: float, stop_type: str) -> None:
    """Mark position as closed and update trade_ledger."""
    pos = _positions.get(trade_id)
    if pos is None:
        return
    now = datetime.now(timezone.utc)
    pos.is_open = False
    pos.exit_price = exit_price
    pos.exit_time = now
    pos.stop_type = stop_type

    hold_sec = int((now - pos.entry_time).total_seconds())
    gross_pnl = pos.unrealized(exit_price)

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            UPDATE trade_ledger
            SET ts_exit = ?, exit_price = ?, gross_pnl = ?,
                net_pnl = ?, hold_duration_sec = ?, stop_type = ?
            WHERE id = ?
        """, (
            now.isoformat().replace("+00:00", "Z"),
            exit_price,
            round(gross_pnl, 2),
            round(gross_pnl, 2),  # commission already deducted at entry
            hold_sec,
            stop_type,
            trade_id,
        ))
        conn.commit()
        conn.close()
        logger.info(
            "StopManager: closed trade_id=%d stop_type=%s exit=%.4f pnl=%.2f hold=%ds",
            trade_id, stop_type, exit_price, gross_pnl, hold_sec
        )
    except Exception as exc:
        logger.error("StopManager: DB close failed for trade_id=%d: %s", trade_id, exc)

    # Publish exit event to NATS if connection available
    try:
        from heartbeat import _nats_conn
        import asyncio, json as _json
        if _nats_conn is not None:
            payload = {
                "trade_id": trade_id,
                "exit_price": exit_price,
                "stop_type": stop_type,
                "gross_pnl": round(gross_pnl, 2),
                "hold_sec": hold_sec,
            }
            asyncio.get_event_loop().run_until_complete(
                _nats_conn.publish("tsla.alpha.exits", _json.dumps(payload).encode())
            )
    except Exception:
        pass  # NATS not required for stop logic to work


def get_open_positions() -> list[dict]:
    """Return serialisable list of open managed positions."""
    result = []
    now = datetime.now(timezone.utc)
    for pos in _positions.values():
        if not pos.is_open:
            continue
        remaining_sec = max(0, int((pos.time_stop_at - now).total_seconds()))
        result.append({
            "trade_id": pos.trade_id,
            "entry_price": pos.entry_price,
            "entry_time": pos.entry_time.isoformat().replace("+00:00", "Z"),
            "quantity": pos.quantity,
            "direction": pos.direction,
            "strategy": pos.strategy,
            "initial_stop": pos.initial_stop,
            "current_stop": pos.current_stop,
            "target": pos.target,
            "trailing_engaged": pos.trailing_engaged,
            "time_stop_at": pos.time_stop_at.isoformat().replace("+00:00", "Z"),
            "remaining_sec": remaining_sec,
        })
    return result


def manual_close(trade_id: int, exit_price: float) -> bool:
    """User-initiated close (dashboard 'Close Now' button)."""
    if trade_id not in _positions or not _positions[trade_id].is_open:
        return False
    close_position(trade_id, exit_price, "MANUAL")
    return True


def check_all_positions(current_price: float, current_atr: float) -> list[dict]:
    """Check all open positions and fire exits as needed. Returns list of fired exits."""
    fired = []
    for trade_id in list(_positions.keys()):
        pos = _positions.get(trade_id)
        if pos is None or not pos.is_open:
            continue
        should_close, stop_type = update_stops(trade_id, current_price, current_atr)
        if should_close:
            close_position(trade_id, current_price, stop_type)
            fired.append({"trade_id": trade_id, "stop_type": stop_type, "exit_price": current_price})
    return fired

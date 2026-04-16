#!/usr/bin/env python3
"""
Phase 16: Proposal Store — SQLite helpers called by the Go API as subprocesses.

Commands:
  upsert  <json>              — insert/update a trade proposal row
  set_strategy <json>         — persist the user-selected strategy
  get_strategy                — print current strategy JSON
  ledger <date>               — print trade ledger rows for date as JSON
  pnl <date>                  — print daily P&L summary as JSON
  strategy_breakdown <date>   — print per-strategy P&L breakdown as JSON
  override_regime <json>      — write a regime override to process_heartbeats
"""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "alpha.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")

# Allow tests to override the DB path without touching the real DB
_DB_PATH_OVERRIDE: str | None = None

DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", "-2500"))
DAILY_PNL_TARGET = float(os.getenv("DAILY_PNL_TARGET", "10000"))


def _db_path() -> str:
    return _DB_PATH_OVERRIDE if _DB_PATH_OVERRIDE else DB_PATH


def _connect() -> sqlite3.Connection:
    path = _db_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Apply schema idempotently
    if os.path.exists(SCHEMA_PATH):
        with open(SCHEMA_PATH) as f:
            conn.executescript(f.read())
        conn.commit()
    return conn


def _ensure_tables() -> None:
    """Ensure schema is applied — used by tests."""
    _connect().close()


# ── Python-callable API (used by tests and internal callers) ──────────────────

def upsert(data: dict) -> None:
    """Insert or replace a trade proposal row."""
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd_upsert(json.dumps(data))


def get_pending() -> list:
    """Return all pending, non-expired proposals as list of dicts."""
    conn = _connect()
    now = datetime.now(timezone.utc).isoformat()
    rows = conn.execute("""
        SELECT id, ts_created, ts_expires, status, strategy, direction,
               legs, entry_price, stop_price, target_price,
               kelly_fraction, quantity, confidence,
               regime_snapshot, signals_contributing, raw_signal
        FROM trade_proposals
        WHERE status = 'pending' AND ts_expires > ?
        ORDER BY ts_created DESC
    """, (now,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_strategy(strategy: str, locked_by: str = 'user') -> None:
    """Persist user-selected strategy."""
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd_set_strategy(json.dumps({"strategy": strategy, "locked_by": locked_by,
                                     "locked_at": _now()}))


def get_strategy() -> dict | None:
    """Return current strategy dict or None if not set."""
    conn = _connect()
    row = conn.execute(
        "SELECT strategy, locked_at, locked_by FROM selected_strategy WHERE id=1"
    ).fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def upsert_ledger(data: dict) -> None:
    """Insert a trade ledger row (maps test-friendly keys to schema columns)."""
    conn = _connect()
    conn.execute("""
        INSERT INTO trade_ledger
        (ts_entry, strategy, direction, legs,
         entry_price, quantity, gross_pnl, net_pnl,
         regime_at_entry, kelly_fraction, human_override, notes)
        VALUES (:entry_ts, :strategy, :direction, :legs,
                :fill_price, :quantity, :pnl_realised, :pnl_unrealised,
                :regime_at_entry, :kelly_fraction, :human_override, :notes)
    """, {
        'entry_ts':       data.get('entry_ts', _now()),
        'strategy':       data.get('strategy', ''),
        'direction':      data.get('direction', ''),
        'legs':           data.get('legs', '[]'),
        'fill_price':     data.get('fill_price', 0.0),
        'quantity':       data.get('quantity', 0),
        'pnl_realised':   data.get('pnl_realised', 0.0),
        'pnl_unrealised': data.get('pnl_unrealised', 0.0),
        'regime_at_entry':data.get('regime_at_entry', ''),
        'kelly_fraction': data.get('kelly_fraction', 0.0),
        'human_override': str(data.get('human_override', '')),
        'notes':          data.get('notes', ''),
    })
    conn.commit()
    last_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return last_id


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_upsert(raw_json: str) -> None:
    data = json.loads(raw_json)
    conn = _connect()
    conn.execute("""
        INSERT OR REPLACE INTO trade_proposals
        (id, ts_created, ts_expires, status, strategy, direction,
         legs, entry_price, stop_price, target_price, kelly_fraction,
         quantity, confidence, regime_snapshot, signals_contributing, raw_signal)
        VALUES (:id,:ts_created,:ts_expires,:status,:strategy,:direction,
                :legs,:entry_price,:stop_price,:target_price,:kelly_fraction,
                :quantity,:confidence,:regime_snapshot,:signals_contributing,:raw_signal)
    """, {
        "id":                   data.get("id"),
        "ts_created":           data.get("ts_created"),
        "ts_expires":           data.get("ts_expires"),
        "status":               data.get("status", "pending"),
        "strategy":             data.get("strategy"),
        "direction":            data.get("direction"),
        "legs":                 json.dumps(data.get("legs", [])),
        "entry_price":          data.get("entry_price"),
        "stop_price":           data.get("stop_price"),
        "target_price":         data.get("target_price"),
        "kelly_fraction":       data.get("kelly_fraction"),
        "quantity":             data.get("quantity"),
        "confidence":           data.get("confidence"),
        "regime_snapshot":      json.dumps(data.get("regime_snapshot", {})),
        "signals_contributing": json.dumps(data.get("signals_contributing", [])),
        "raw_signal":           json.dumps(data.get("raw_signal", {})),
    })
    conn.commit()
    conn.close()
    print(json.dumps({"ok": True}))


def cmd_set_strategy(raw_json: str) -> None:
    data = json.loads(raw_json)
    conn = _connect()
    conn.execute("""
        INSERT OR REPLACE INTO selected_strategy (id, strategy, locked_at, locked_by)
        VALUES (1, :strategy, :locked_at, 'user')
    """, {"strategy": data["strategy"], "locked_at": data.get("locked_at", _now())})
    conn.commit()
    conn.close()
    print(json.dumps({"ok": True}))


def cmd_get_strategy() -> None:
    conn = _connect()
    row = conn.execute(
        "SELECT strategy, locked_at FROM selected_strategy WHERE id=1"
    ).fetchone()
    conn.close()
    if row:
        print(json.dumps({"strategy": row["strategy"], "locked_at": row["locked_at"]}))
    else:
        print(json.dumps({"strategy": None}))


def cmd_ledger(date: str) -> None:
    conn = _connect()
    rows = conn.execute("""
        SELECT id, ts_entry, ts_exit, strategy, regime_at_entry, direction,
               legs, entry_price, exit_price, quantity, commission,
               gross_pnl, net_pnl, hold_duration_sec, stop_type,
               confidence_at_entry, kelly_fraction, human_override,
               slippage, tags, notes
        FROM trade_ledger
        WHERE ts_entry LIKE ?
        ORDER BY ts_entry DESC
    """, (date + "%",)).fetchall()
    conn.close()

    trades = []
    for r in rows:
        trades.append({
            "id": r["id"],
            "ts_entry": r["ts_entry"],
            "ts_exit": r["ts_exit"],
            "strategy": r["strategy"],
            "regime_at_entry": r["regime_at_entry"],
            "direction": r["direction"],
            "legs": _parse_json(r["legs"]),
            "entry_price": r["entry_price"],
            "exit_price": r["exit_price"],
            "quantity": r["quantity"],
            "commission": r["commission"],
            "gross_pnl": r["gross_pnl"],
            "net_pnl": r["net_pnl"],
            "hold_duration_sec": r["hold_duration_sec"],
            "stop_type": r["stop_type"],
            "confidence_at_entry": r["confidence_at_entry"],
            "kelly_fraction": r["kelly_fraction"],
            "human_override": r["human_override"],
            "slippage": r["slippage"],
            "tags": _parse_json(r["tags"]),
            "notes": r["notes"],
        })
    print(json.dumps({"date": date, "trades": trades}))


def cmd_pnl(date: str) -> None:
    conn = _connect()
    rows = conn.execute("""
        SELECT ts_entry, strategy, COALESCE(net_pnl, 0) as pnl
        FROM trade_ledger
        WHERE ts_entry LIKE ? AND net_pnl IS NOT NULL
        ORDER BY ts_entry ASC
    """, (date + "%",)).fetchall()
    conn.close()

    total_pnl = 0.0
    winners, losers = 0, 0
    waterfall = []

    for r in rows:
        pnl = r["pnl"]
        total_pnl += pnl
        if pnl >= 0:
            winners += 1
        else:
            losers += 1
        waterfall.append({
            "time": r["ts_entry"],
            "strategy": r["strategy"],
            "pnl": pnl,
            "cumul": total_pnl,
        })

    circuit_broken = total_pnl <= DAILY_LOSS_LIMIT
    target_pct = (total_pnl / DAILY_PNL_TARGET * 100) if DAILY_PNL_TARGET > 0 else 0
    loss_used_pct = 0.0
    if DAILY_LOSS_LIMIT < 0 and total_pnl < 0:
        loss_used_pct = (total_pnl / DAILY_LOSS_LIMIT) * 100

    print(json.dumps({
        "date": date,
        "total_pnl": round(total_pnl, 2),
        "daily_target": DAILY_PNL_TARGET,
        "target_pct": round(target_pct, 1),
        "daily_loss_limit": DAILY_LOSS_LIMIT,
        "loss_used_pct": round(loss_used_pct, 1),
        "circuit_broken": circuit_broken,
        "winners": winners,
        "losers": losers,
        "waterfall": waterfall,
        "updated_at": _now(),
    }))


def cmd_strategy_breakdown(date: str) -> None:
    conn = _connect()
    rows = conn.execute("""
        SELECT strategy,
               COUNT(*) as trades,
               SUM(CASE WHEN net_pnl >= 0 THEN 1 ELSE 0 END) as winners,
               SUM(CASE WHEN net_pnl < 0  THEN 1 ELSE 0 END) as losers,
               COALESCE(SUM(net_pnl), 0) as net_pnl
        FROM trade_ledger
        WHERE ts_entry LIKE ?
        GROUP BY strategy
        ORDER BY net_pnl DESC
    """, (date + "%",)).fetchall()
    conn.close()

    strategies = []
    for r in rows:
        win_rate = (r["winners"] / r["trades"] * 100) if r["trades"] > 0 else 0
        strategies.append({
            "strategy": r["strategy"],
            "trades": r["trades"],
            "winners": r["winners"],
            "losers": r["losers"],
            "net_pnl": round(r["net_pnl"], 2),
            "win_rate": round(win_rate, 1),
        })
    print(json.dumps({"date": date, "strategies": strategies}))


def cmd_override_regime(raw_json: str) -> None:
    data = json.loads(raw_json)
    regime = data.get("regime", "UNCERTAIN")

    strategy_map = {
        "TRENDING": "MOMENTUM", "FLAT": "IRON_CONDOR",
        "CHOPPY": "WAVE_RIDER", "EVENT_DRIVEN": "STRADDLE", "UNCERTAIN": "IRON_CONDOR",
    }
    now = _now()
    override = {
        "regime": regime,
        "confidence": 1.0,
        "source": "user_override",
        "overridden_at": now,
        "recommended_strategy": strategy_map.get(regime, "IRON_CONDOR"),
        "factors": [{"name": "User Override", "contribution": 1.0, "description": f"Manually set to {regime}"}],
        "refreshed_at": now,
    }

    conn = _connect()
    conn.execute(
        "INSERT INTO process_heartbeats (component, ts, status, detail) VALUES (?, ?, ?, ?)",
        ("regime_classifier", now, "ok", json.dumps(override)),
    )
    conn.commit()
    conn.close()
    print(json.dumps({"ok": True, "regime": regime}))


# ── helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_json(val):
    if not val:
        return None
    try:
        return json.loads(val)
    except Exception:
        return val


# ── dispatch ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "command required"}), file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]
    arg = sys.argv[2] if len(sys.argv) > 2 else ""

    try:
        if cmd == "upsert":
            cmd_upsert(arg)
        elif cmd == "set_strategy":
            cmd_set_strategy(arg)
        elif cmd == "get_strategy":
            cmd_get_strategy()
        elif cmd == "ledger":
            cmd_ledger(arg or datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        elif cmd == "pnl":
            cmd_pnl(arg or datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        elif cmd == "strategy_breakdown":
            cmd_strategy_breakdown(arg or datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        elif cmd == "override_regime":
            cmd_override_regime(arg)
        else:
            print(json.dumps({"error": f"unknown command: {cmd}"}), file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

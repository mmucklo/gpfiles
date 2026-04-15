"""
Process Heartbeat — shared liveness pulse for all long-running components.

Usage (sync context, e.g. ingestion modules):
    from heartbeat import emit_heartbeat
    emit_heartbeat("premarket", status="ok", detail="fetched 4 tickers")

Usage (async context, e.g. publisher.py):
    from heartbeat import emit_heartbeat_async
    await emit_heartbeat_async("publisher", status="ok")

Both paths write directly to the SQLite DB (~/tsla_alpha.db) and optionally
publish to NATS subject 'system.heartbeat' when a connection is available.

The sync path uses a short-lived sqlite3 connection so it is safe to call
from any thread or process that does NOT already hold the DB connection.
The async path delegates to DataLogger's queue so the publisher hot-path
never blocks on disk I/O.
"""
import os
import sqlite3
import time
import json
from datetime import datetime, timezone

DB_PATH = os.path.expanduser("~/tsla_alpha.db")

# NATS connection reference — set by the publisher once connected.
_nats_conn = None
_process_start = time.time()


def set_nats_conn(nc) -> None:
    """Register the shared NATS connection so heartbeats are also published."""
    global _nats_conn
    _nats_conn = nc


def _uptime() -> int:
    return int(time.time() - _process_start)


def _isotime() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ── Sync path (ingestion modules, Go subprocess calls) ───────────────────────

def emit_heartbeat(
    component: str,
    status: str = "ok",
    detail: str | None = None,
    db_path: str = DB_PATH,
) -> None:
    """Write a heartbeat row to SQLite (synchronous, short-lived connection).

    Safe to call from any thread; uses WAL so concurrent reads/writes are fine.
    Silently swallows all exceptions — a failed heartbeat write must never
    crash the calling component.
    """
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """INSERT INTO process_heartbeats (component, ts, status, detail, pid, uptime_sec)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (component, _isotime(), status, detail, os.getpid(), _uptime()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # heartbeat writes must never crash the caller

    # Also write system_alert if status is error, to surface in event feed
    if status == "error":
        _write_system_alert(component, status, detail or f"{component} reported error", db_path)


def _write_system_alert(
    component: str,
    status: str,
    message: str,
    db_path: str = DB_PATH,
) -> None:
    """Append a system_alert row. Called when a component reports error status."""
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """INSERT INTO system_alerts (ts, component, status, message)
               VALUES (?, ?, ?, ?)""",
            (_isotime(), component, status, message),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def emit_heartbeat_recovered(
    component: str,
    db_path: str = DB_PATH,
) -> None:
    """Log a [HEARTBEAT-RECOVERED] alert when a component returns to ok after outage."""
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """INSERT INTO system_alerts (ts, component, status, message)
               VALUES (?, ?, ?, ?)""",
            (_isotime(), component, "ok", f"[HEARTBEAT-RECOVERED] {component} returned to ok"),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ── Async path (publisher.py / DataLogger) ───────────────────────────────────

async def emit_heartbeat_async(
    component: str,
    status: str = "ok",
    detail: str | None = None,
    logger=None,
) -> None:
    """Async heartbeat: queues a write via DataLogger and publishes to NATS.

    Args:
        component: Component name, e.g. "publisher".
        status: "ok" | "degraded" | "error"
        detail: Optional error/note string.
        logger: DataLogger instance — if None, falls back to sync sqlite write.
    """
    if logger is not None:
        await logger.log_heartbeat(
            component=component,
            status=status,
            detail=detail,
            pid=os.getpid(),
            uptime_sec=_uptime(),
        )
        if status == "error":
            await logger.log_system_alert(
                component=component,
                status=status,
                message=detail or f"{component} reported error",
            )
    else:
        # Fallback: sync write (e.g. during shutdown)
        emit_heartbeat(component, status, detail)

    # Publish to NATS if connection is available
    if _nats_conn is not None:
        try:
            payload = json.dumps({
                "component": component,
                "ts": _isotime(),
                "status": status,
                "detail": detail,
                "pid": os.getpid(),
                "uptime_sec": _uptime(),
            }).encode()
            await _nats_conn.publish("system.heartbeat", payload)
        except Exception:
            pass  # NATS publish is best-effort


# ── Signal rejection tracking ─────────────────────────────────────────────────

# Full schema for signal_rejections (Phase 14.3 — rich drill-down context).
_SIGNAL_REJECTIONS_DDL = """
CREATE TABLE IF NOT EXISTS signal_rejections (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                       TEXT NOT NULL,
    -- Phase 14.1 columns (always populated)
    model                    TEXT NOT NULL,
    opt_type                 TEXT NOT NULL,
    archetype                TEXT NOT NULL,
    reason                   TEXT NOT NULL,
    expiry                   TEXT,
    -- Phase 14.3 columns (nullable — old rows will have NULL here)
    model_id                 TEXT,
    direction                TEXT,
    confidence               REAL,
    ticker                   TEXT,
    option_type              TEXT,
    expiration_date          TEXT,
    target_strike_attempted  REAL,
    spot_at_rejection        REAL,
    reason_code              TEXT,
    reason_detail            TEXT,
    chain_snapshot           TEXT,
    strike_selector_breakdown TEXT,
    chop_regime_at_rejection TEXT,
    regime_context           TEXT
)
"""

# Columns added in Phase 14.3 — applied via ALTER TABLE to existing installs.
_NEW_COLUMNS_14_3 = [
    ("model_id",                 "TEXT"),
    ("direction",                "TEXT"),
    ("confidence",               "REAL"),
    ("ticker",                   "TEXT"),
    ("option_type",              "TEXT"),
    ("expiration_date",          "TEXT"),
    ("target_strike_attempted",  "REAL"),
    ("spot_at_rejection",        "REAL"),
    ("reason_code",              "TEXT"),
    ("reason_detail",            "TEXT"),
    ("chain_snapshot",           "TEXT"),
    ("strike_selector_breakdown","TEXT"),
    ("chop_regime_at_rejection", "TEXT"),
    ("regime_context",           "TEXT"),
]


def _migrate_rejections_table(conn: sqlite3.Connection) -> None:
    """Add Phase-14.3 columns to existing signal_rejections tables.

    Uses try/except per column because SQLite doesn't support IF NOT EXISTS
    in ALTER TABLE ADD COLUMN.  Each column failure is silently skipped.
    """
    for col, col_type in _NEW_COLUMNS_14_3:
        try:
            conn.execute(f"ALTER TABLE signal_rejections ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass  # column already exists — expected for Phase-14.3+ installs


def emit_rejection(
    model: str,
    opt_type: str,
    archetype: str,
    reason: str,
    expiry: str | None = None,
    # Phase 14.3 extended context (all optional for backward compat)
    model_id: str | None = None,
    direction: str | None = None,
    confidence: float | None = None,
    ticker: str | None = None,
    option_type: str | None = None,
    expiration_date: str | None = None,
    target_strike_attempted: float | None = None,
    spot_at_rejection: float | None = None,
    reason_code: str | None = None,
    reason_detail: str | None = None,
    chain_snapshot: str | None = None,
    strike_selector_breakdown: str | None = None,
    chop_regime_at_rejection: str | None = None,
    regime_context: str | None = None,
    db_path: str = DB_PATH,
) -> None:
    """Record a dropped signal to the signal_rejections table.

    Phase 14.1 fields (model, opt_type, archetype, reason, expiry) are always
    written.  Phase 14.3 fields are written when provided and are NULL in rows
    emitted by pre-14.3 publisher code.

    Failures are logged loudly to stderr and retried once; a second failure is
    swallowed — a failed rejection write must never crash the publisher.
    """
    ts = _isotime()

    def _write(conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(_SIGNAL_REJECTIONS_DDL)
        # Ensure system_alerts exists (created by heartbeat.py on first heartbeat,
        # but may not exist in isolated test databases).
        conn.execute(
            """CREATE TABLE IF NOT EXISTS system_alerts (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        TEXT    NOT NULL,
                component TEXT    NOT NULL,
                status    TEXT    NOT NULL,
                message   TEXT    NOT NULL
            )"""
        )
        _migrate_rejections_table(conn)
        conn.execute(
            """INSERT INTO signal_rejections
               (ts, model, opt_type, archetype, reason, expiry,
                model_id, direction, confidence, ticker, option_type,
                expiration_date, target_strike_attempted, spot_at_rejection,
                reason_code, reason_detail, chain_snapshot,
                strike_selector_breakdown, chop_regime_at_rejection, regime_context)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ts, model, opt_type, archetype, reason, expiry or "",
                model_id or model, direction, confidence, ticker or "TSLA",
                option_type or opt_type, expiration_date or expiry,
                target_strike_attempted, spot_at_rejection,
                reason_code, reason_detail, chain_snapshot,
                strike_selector_breakdown, chop_regime_at_rejection, regime_context,
            ),
        )
        # Also write a system_alert so the audit feed surfaces this rejection.
        rc = reason_code or reason[:40]
        mid = model_id or model
        conn.execute(
            """INSERT INTO system_alerts (ts, component, status, message)
               VALUES (?, ?, ?, ?)""",
            (ts, "publisher", "ok",
             f"[SIGNAL-REJECTED] model={mid} reason={rc}"),
        )
        # Keep last 500 rejection rows; trim older ones.
        conn.execute(
            """DELETE FROM signal_rejections
               WHERE id NOT IN (SELECT id FROM signal_rejections ORDER BY id DESC LIMIT 500)"""
        )
        conn.commit()

    # Attempt 1
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        _write(conn)
        conn.close()
        return
    except Exception as exc:
        import sys
        print(f"[EMIT-REJECTION] write failed (attempt 1): {exc}", file=sys.stderr)
        try:
            conn.close()
        except Exception:
            pass

    # Retry once
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        _write(conn)
        conn.close()
    except Exception as exc2:
        import sys
        print(f"[EMIT-REJECTION] write failed (attempt 2, giving up): {exc2}", file=sys.stderr)

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

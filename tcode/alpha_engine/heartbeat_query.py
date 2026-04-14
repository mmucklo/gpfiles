#!/usr/bin/env python3
"""
Query process_heartbeats + system_alerts for the /api/system/heartbeats endpoint.

Outputs a JSON object with per-component status, age_sec, expected_max_age_sec, etc.
Called by the Go API as a subprocess.

Usage: python3 heartbeat_query.py
"""
import json
import os
import sqlite3
import time
from datetime import datetime, timezone

DB_PATH = os.path.expanduser("~/tsla_alpha.db")

# Expected max heartbeat age per component (seconds).
# If age > expected_max, component is degraded; if age > 3×, it's error.
EXPECTED_MAX_AGE: dict[str, int] = {
    "publisher":          30,
    "intel_refresh":      300,
    "options_chain_api":  120,
    "premarket":          120,    # only relevant during premarket window
    "congress_trades":    3600,
    "correlation_regime": 3600,
    "macro_regime":       300,
    "engine_subscriber":  90,
    "engine_ibkr_status": 180,
}

# Premarket window: 04:00–09:30 ET
def _is_premarket_window() -> bool:
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo("America/New_York")
    except Exception:
        return True
    et = datetime.now(tz)
    t = et.hour * 60 + et.minute
    return 240 <= t < 570  # 4:00–9:30 AM ET


def _compute_status(component: str, age_sec: float, last_detail: str | None) -> str:
    """Compute ok / degraded / error based on age relative to expected cadence."""
    # Premarket special case: if outside window, always ok
    if component == "premarket" and not _is_premarket_window():
        return "ok"
    expected = EXPECTED_MAX_AGE.get(component, 300)
    if age_sec <= expected:
        return "ok"
    if age_sec <= 3 * expected:
        return "degraded"
    return "error"


def query_heartbeats() -> dict:
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    now_epoch = time.time()

    components: dict[str, dict] = {}

    if not os.path.exists(DB_PATH):
        # DB not yet initialized — all unknown/error
        for comp in EXPECTED_MAX_AGE:
            components[comp] = {
                "status": "error",
                "last_ts": None,
                "age_sec": None,
                "expected_max_age_sec": EXPECTED_MAX_AGE[comp],
                "pid": None,
                "uptime_sec": None,
                "detail": "db_not_found",
            }
        return {"ts": now_ts, "components": components}

    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")

        # Latest heartbeat per component
        rows = conn.execute(
            """
            SELECT component, ts, status, detail, pid, uptime_sec
            FROM process_heartbeats
            WHERE id IN (
                SELECT MAX(id) FROM process_heartbeats GROUP BY component
            )
            """
        ).fetchall()
        conn.close()
    except Exception as e:
        for comp in EXPECTED_MAX_AGE:
            components[comp] = {
                "status": "error",
                "last_ts": None,
                "age_sec": None,
                "expected_max_age_sec": EXPECTED_MAX_AGE[comp],
                "pid": None,
                "uptime_sec": None,
                "detail": f"db_error:{e}",
            }
        return {"ts": now_ts, "components": components}

    seen: dict[str, dict] = {}
    for row in rows:
        comp = row["component"]
        ts_str = row["ts"]
        try:
            # Parse "YYYY-MM-DD HH:MM:SS" as UTC
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            age_sec = now_epoch - dt.timestamp()
        except Exception:
            age_sec = 999999

        status = _compute_status(comp, age_sec, row["detail"])

        # Premarket off-hours special: show ok with detail skipped:off-hours
        if comp == "premarket" and not _is_premarket_window():
            detail = row["detail"] or "skipped:off-hours"
        else:
            detail = row["detail"]

        seen[comp] = {
            "status": status,
            "last_ts": ts_str,
            "age_sec": round(age_sec, 1),
            "expected_max_age_sec": EXPECTED_MAX_AGE.get(comp, 300),
            "pid": row["pid"],
            "uptime_sec": row["uptime_sec"],
            "detail": detail,
        }

    # Fill in never-seen components as error
    for comp, max_age in EXPECTED_MAX_AGE.items():
        if comp not in seen:
            # Premarket off-hours: show ok/skipped rather than error when never pulsed
            if comp == "premarket" and not _is_premarket_window():
                seen[comp] = {
                    "status": "ok",
                    "last_ts": None,
                    "age_sec": None,
                    "expected_max_age_sec": max_age,
                    "pid": None,
                    "uptime_sec": None,
                    "detail": "skipped:off-hours",
                }
            else:
                seen[comp] = {
                    "status": "error",
                    "last_ts": None,
                    "age_sec": None,
                    "expected_max_age_sec": max_age,
                    "pid": None,
                    "uptime_sec": None,
                    "detail": "no_heartbeat_received",
                }

    return {"ts": now_ts, "components": seen}


def query_sparkline(component: str, limit: int = 10) -> list[dict]:
    """Return the last `limit` heartbeat rows for a component (for drill-down sparkline)."""
    if not os.path.exists(DB_PATH):
        return []
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT ts, status, detail, pid, uptime_sec
               FROM process_heartbeats
               WHERE component = ?
               ORDER BY id DESC LIMIT ?""",
            (component, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def query_recent_alerts(limit: int = 5) -> list[dict]:
    """Return the latest system_alerts rows for the event feed."""
    if not os.path.exists(DB_PATH):
        return []
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT ts, component, status, message
               FROM system_alerts
               ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def query_rejections(hours: int = 1, limit: int = 100) -> dict:
    """Return signal rejections from the last `hours` hours.

    Returns:
        {"count": int, "items": [{"ts", "model", "opt_type", "archetype", "reason", "expiry"}, ...]}
    """
    if not os.path.exists(DB_PATH):
        return {"count": 0, "items": []}
    try:
        cutoff = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        # Compute cutoff as UTC string hours ago
        import datetime as _dt
        cutoff_dt = datetime.now(timezone.utc) - _dt.timedelta(hours=hours)
        cutoff_str = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")

        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            rows = conn.execute(
                """SELECT ts, model, opt_type, archetype, reason, expiry
                   FROM signal_rejections
                   WHERE ts >= ?
                   ORDER BY id DESC LIMIT ?""",
                (cutoff_str, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # Table doesn't exist yet (no rejections ever written)
            return {"count": 0, "items": []}
        finally:
            conn.close()
        items = [dict(r) for r in rows]
        return {"count": len(items), "items": items}
    except Exception as e:
        return {"count": 0, "items": [], "error": str(e)}


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "heartbeats"
    if mode == "sparkline" and len(sys.argv) > 2:
        print(json.dumps(query_sparkline(sys.argv[2])))
    elif mode == "alerts":
        print(json.dumps(query_recent_alerts()))
    elif mode == "rejections":
        hours = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        print(json.dumps(query_rejections(hours=hours)))
    else:
        print(json.dumps(query_heartbeats()))

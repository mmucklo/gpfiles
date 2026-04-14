"""
signal_feedback.py — Human-in-the-loop signal annotation store.

Subcommands:
    add            POST a new feedback row (prints JSON with id + ts_feedback)
    get_for_signal GET all feedback rows for one signal_id (newest first)
    get_recent     GET recent feedback across all signals (optional tag/action/since filters)
    get_digest     GET aggregated feedback grouped by tag + model + archetype
    resolve        PATCH a feedback row as addressed

Comment text is stored verbatim — no trimming, no normalization.
Rows are immutable: never deleted, only resolved via resolved_by/resolved_at.

CLI usage (called from Go via exec.Command):
    python alpha_engine/ingestion/signal_feedback.py add       <json_args>
    python alpha_engine/ingestion/signal_feedback.py get_for_signal <json_args>
    python alpha_engine/ingestion/signal_feedback.py get_recent     <json_args>
    python alpha_engine/ingestion/signal_feedback.py get_digest     <json_args>
    python alpha_engine/ingestion/signal_feedback.py resolve        <json_args>
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

DB_PATH = os.path.expanduser("~/tsla_alpha.db")

VALID_TAGS = {
    "bad_entry", "bad_strike", "wrong_direction", "right_idea_wrong_size",
    "expired_worthless", "late_signal", "commission_dominated", "good_signal", "other",
}

VALID_ACTIONS = {"COMMENT", "CANCEL", "FOLLOWUP", "MARK_WINNER", "MARK_LOSER"}


def _get_conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_add(args: dict) -> dict:
    """
    Add a new feedback row.

    Required args:
        signal_id       str  — fingerprint of the signal
        signal_snapshot dict — JSON snapshot of the signal at feedback time
        user_comment    str  — verbatim comment (non-empty)
        action          str  — COMMENT|CANCEL|FOLLOWUP|MARK_WINNER|MARK_LOSER
    Optional:
        tag             str  — one of VALID_TAGS
        reviewer        str  — default 'user'
        db_path         str  — override DB path (for tests)
    """
    signal_id = args.get("signal_id", "").strip()
    if not signal_id:
        return {"error": "signal_id is required"}

    user_comment = args.get("user_comment", "")
    # Do NOT strip/normalize — comment is stored verbatim
    if not user_comment:
        return {"error": "user_comment must be non-empty"}

    action = args.get("action", "")
    if action not in VALID_ACTIONS:
        return {"error": f"action must be one of {sorted(VALID_ACTIONS)}, got {action!r}"}

    tag = args.get("tag") or None
    if tag is not None and tag not in VALID_TAGS:
        return {"error": f"tag must be one of {sorted(VALID_TAGS)} or null, got {tag!r}"}

    snapshot = args.get("signal_snapshot", {})
    if isinstance(snapshot, dict):
        snapshot_json = json.dumps(snapshot)
    elif isinstance(snapshot, str):
        # Validate it's valid JSON before storing
        try:
            json.loads(snapshot)
            snapshot_json = snapshot
        except json.JSONDecodeError:
            return {"error": "signal_snapshot must be valid JSON"}
    else:
        snapshot_json = json.dumps({})

    reviewer = args.get("reviewer", "user")
    ts = _now_utc()
    db_path = args.get("db_path", DB_PATH)

    conn = _get_conn(db_path)
    try:
        cursor = conn.execute(
            """
            INSERT INTO signal_feedback
                (signal_id, signal_snapshot, ts_feedback, user_comment, tag, action, reviewer)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (signal_id, snapshot_json, ts, user_comment, tag, action, reviewer),
        )
        conn.commit()
        row_id = cursor.lastrowid
    finally:
        conn.close()

    return {"id": row_id, "ts_feedback": ts, "signal_id": signal_id, "action": action}


def cmd_get_for_signal(args: dict) -> list:
    """
    Return all feedback rows for a signal_id, newest first.

    Required: signal_id
    Optional: db_path
    """
    signal_id = args.get("signal_id", "").strip()
    if not signal_id:
        return []

    db_path = args.get("db_path", DB_PATH)
    conn = _get_conn(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, signal_id, ts_feedback, user_comment, tag, action,
                   reviewer, resolved_by, resolved_at
            FROM signal_feedback
            WHERE signal_id = ?
            ORDER BY ts_feedback DESC
            """,
            (signal_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def cmd_get_recent(args: dict) -> dict:
    """
    Return recent feedback across all signals.

    Optional args:
        since   str  — ISO 8601 UTC lower bound (default: all time)
        tag     str  — filter by tag
        action  str  — filter by action
        limit   int  — max rows (default 50)
        offset  int  — for pagination (default 0)
        db_path str
    """
    db_path = args.get("db_path", DB_PATH)
    since = args.get("since") or "1970-01-01T00:00:00Z"
    tag = args.get("tag") or None
    action = args.get("action") or None
    limit = int(args.get("limit", 50))
    offset = int(args.get("offset", 0))

    query = """
        SELECT id, signal_id, ts_feedback, user_comment, tag, action,
               reviewer, resolved_by, resolved_at
        FROM signal_feedback
        WHERE ts_feedback >= ?
    """
    params: list = [since]
    if tag:
        query += " AND tag = ?"
        params.append(tag)
    if action:
        query += " AND action = ?"
        params.append(action)
    query += " ORDER BY ts_feedback DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    count_query = "SELECT COUNT(*) FROM signal_feedback WHERE ts_feedback >= ?"
    count_params: list = [since]
    if tag:
        count_query += " AND tag = ?"
        count_params.append(tag)
    if action:
        count_query += " AND action = ?"
        count_params.append(action)

    conn = _get_conn(db_path)
    try:
        rows = conn.execute(query, params).fetchall()
        total = conn.execute(count_query, count_params).fetchone()[0]
        unresolved = conn.execute(
            "SELECT COUNT(*) FROM signal_feedback WHERE resolved_by IS NULL AND ts_feedback >= ?",
            [since],
        ).fetchone()[0]
    finally:
        conn.close()

    return {
        "rows": [dict(r) for r in rows],
        "total": total,
        "unresolved": unresolved,
        "limit": limit,
        "offset": offset,
    }


def cmd_get_digest(args: dict) -> dict:
    """
    Return aggregated feedback grouped by tag / model / archetype.
    Intended for mayor consumption when refining signal logic.

    Optional args:
        since   str  — ISO 8601 UTC lower bound (default all)
        db_path str
    """
    db_path = args.get("db_path", DB_PATH)
    since = args.get("since") or "1970-01-01T00:00:00Z"

    conn = _get_conn(db_path)
    try:
        # Total count
        total = conn.execute(
            "SELECT COUNT(*) FROM signal_feedback WHERE ts_feedback >= ?", [since]
        ).fetchone()[0]

        # By tag
        by_tag_rows = conn.execute(
            """
            SELECT tag, COUNT(*) as cnt
            FROM signal_feedback
            WHERE ts_feedback >= ? AND tag IS NOT NULL
            GROUP BY tag
            ORDER BY cnt DESC
            """,
            [since],
        ).fetchall()
        by_tag = {r["tag"]: r["cnt"] for r in by_tag_rows}

        # By model (extracted from signal_snapshot JSON)
        # Use JSON extract if available (SQLite 3.38+), else collect all snapshots
        by_model_rows = conn.execute(
            """
            SELECT
                json_extract(signal_snapshot, '$.model_id') as model_id,
                COUNT(*) as cnt
            FROM signal_feedback
            WHERE ts_feedback >= ?
              AND json_extract(signal_snapshot, '$.model_id') IS NOT NULL
            GROUP BY model_id
            ORDER BY cnt DESC
            """,
            [since],
        ).fetchall()
        by_model = {r["model_id"]: r["cnt"] for r in by_model_rows}

        # Cancelled signal ids
        cancelled = conn.execute(
            """
            SELECT DISTINCT signal_id FROM signal_feedback
            WHERE action = 'CANCEL' AND ts_feedback >= ?
            """,
            [since],
        ).fetchall()
        cancelled_ids = [r["signal_id"] for r in cancelled]

        # Unresolved comments (action=COMMENT, no resolved_by)
        unresolved_rows = conn.execute(
            """
            SELECT id, signal_id, user_comment, tag, ts_feedback
            FROM signal_feedback
            WHERE resolved_by IS NULL AND ts_feedback >= ?
            ORDER BY ts_feedback DESC
            LIMIT 50
            """,
            [since],
        ).fetchall()
        unresolved_comments = [
            {
                "id": r["id"],
                "signal_id": r["signal_id"],
                "comment": r["user_comment"],  # verbatim
                "tag": r["tag"],
                "ts": r["ts_feedback"],
            }
            for r in unresolved_rows
        ]

    finally:
        conn.close()

    return {
        "since": since,
        "total_feedback": total,
        "by_tag": by_tag,
        "by_model": by_model,
        "cancelled_signals": cancelled_ids,
        "unresolved_comments": unresolved_comments,
    }


def cmd_resolve(args: dict) -> dict:
    """
    Mark a feedback row as resolved.

    Required: id (int), resolved_by (str)
    Optional: db_path
    """
    row_id = args.get("id")
    if row_id is None:
        return {"error": "id is required"}
    resolved_by = args.get("resolved_by", "").strip()
    if not resolved_by:
        return {"error": "resolved_by is required"}

    resolved_at = _now_utc()
    db_path = args.get("db_path", DB_PATH)

    conn = _get_conn(db_path)
    try:
        conn.execute(
            "UPDATE signal_feedback SET resolved_by = ?, resolved_at = ? WHERE id = ?",
            (resolved_by, resolved_at, row_id),
        )
        conn.commit()
        affected = conn.execute(
            "SELECT changes()"
        ).fetchone()[0]
    finally:
        conn.close()

    if affected == 0:
        return {"error": f"no row with id={row_id}"}
    return {"id": row_id, "resolved_by": resolved_by, "resolved_at": resolved_at}


COMMANDS = {
    "add": cmd_add,
    "get_for_signal": cmd_get_for_signal,
    "get_recent": cmd_get_recent,
    "get_digest": cmd_get_digest,
    "resolve": cmd_resolve,
}


def main():
    if len(sys.argv) < 3:
        print(json.dumps({"error": "usage: signal_feedback.py <subcommand> <json_args>"}))
        sys.exit(1)

    subcommand = sys.argv[1]
    if subcommand not in COMMANDS:
        print(json.dumps({"error": f"unknown subcommand {subcommand!r}", "valid": list(COMMANDS)}))
        sys.exit(1)

    try:
        args = json.loads(sys.argv[2])
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"args must be JSON: {e}"}))
        sys.exit(1)

    result = COMMANDS[subcommand](args)
    print(json.dumps(result))


if __name__ == "__main__":
    main()

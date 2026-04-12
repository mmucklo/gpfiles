"""
TSLA Alpha Engine: Model Scorecard + Loss Tagger
Computes per-model performance stats from closed_trades.

CLI:
  python3 alpha_engine/data/scorecard.py scorecard
  python3 alpha_engine/data/scorecard.py losses
  python3 alpha_engine/data/scorecard.py tag <trade_id> <tag> [notes]

Valid loss tags: bad_signal bad_timing macro_event stop_loss
                 expiry_decay oversize manual_error unknown
"""
import json
import math
import os
import sqlite3
import sys

DB_PATH = os.path.expanduser("~/tsla_alpha.db")

VALID_TAGS = {
    "bad_signal", "bad_timing", "macro_event", "stop_loss",
    "expiry_decay", "oversize", "manual_error", "unknown",
}


def _conn(db_path: str) -> sqlite3.Connection:
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    return c


def _sharpe(pnls: list[float]) -> float:
    n = len(pnls)
    if n < 2:
        return 0.0
    mean = sum(pnls) / n
    variance = sum((p - mean) ** 2 for p in pnls) / (n - 1)
    std = math.sqrt(variance)
    return round((mean / std) * math.sqrt(252), 4) if std > 0 else 0.0


# ── public API ────────────────────────────────────────────────────────────────

def get_scorecard(db_path: str = DB_PATH) -> list[dict]:
    """Per-model performance stats from closed_trades."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM closed_trades ORDER BY exit_ts"
        ).fetchall()

    # Group by model_id
    models: dict[str, list] = {}
    for r in rows:
        mid = r["model_id"] or "unknown"
        models.setdefault(mid, []).append(dict(r))

    result = []
    for model_id, trades in models.items():
        pnls       = [t["pnl"] or 0.0 for t in trades]
        wins       = [t for t in trades if (t["pnl"] or 0) > 0]
        losses     = [t for t in trades if (t["pnl"] or 0) <= 0]
        confs      = [t["confidence_at_entry"] or 0.0 for t in trades]
        n          = len(trades)
        win_count  = len(wins)
        loss_count = len(losses)

        # High-confidence calibration (conf > 0.7)
        high_conf  = [t for t in trades if (t["confidence_at_entry"] or 0) > 0.7]
        hc_wins    = [t for t in high_conf if (t["pnl"] or 0) > 0]
        hc_win_rate = (len(hc_wins) / len(high_conf)) if high_conf else None

        # Loss tag frequency
        tag_counts: dict[str, int] = {}
        for t in losses:
            tag = t.get("loss_tag") or "untagged"
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
        common_loss_tags = sorted(
            [{"tag": k, "count": v} for k, v in tag_counts.items()],
            key=lambda x: -x["count"]
        )

        result.append({
            "model_id":      model_id,
            "trade_count":   n,
            "win_count":     win_count,
            "loss_count":    loss_count,
            "win_rate":      round(win_count / n, 4) if n else 0.0,
            "total_pnl":     round(sum(pnls), 2),
            "avg_pnl":       round(sum(pnls) / n, 2) if n else 0.0,
            "best_trade":    round(max(pnls), 2) if pnls else 0.0,
            "worst_trade":   round(min(pnls), 2) if pnls else 0.0,
            "avg_confidence": round(sum(confs) / n, 4) if n else 0.0,
            "sharpe":        _sharpe(pnls),
            "confidence_calibration": {
                "high_conf_trade_count": len(high_conf),
                "high_conf_win_rate":    round(hc_win_rate, 4) if hc_win_rate is not None else None,
            },
            "common_loss_tags": common_loss_tags,
        })

    # Sort by total_pnl desc
    result.sort(key=lambda x: -x["total_pnl"])
    return result


def get_loss_summary(db_path: str = DB_PATH) -> dict:
    """Aggregate loss stats across all models."""
    with _conn(db_path) as conn:
        losses = conn.execute(
            "SELECT * FROM closed_trades WHERE pnl < 0 OR pnl = 0"
        ).fetchall()
        losses = [dict(r) for r in losses]

    if not losses:
        return {
            "total_losses": 0, "total_loss_amount": 0.0,
            "avg_loss": 0.0, "loss_tags": {},
        }

    total_amount = sum(r["pnl"] or 0.0 for r in losses)
    tag_counts: dict[str, int] = {}
    for r in losses:
        tag = r.get("loss_tag") or "untagged"
        tag_counts[tag] = tag_counts.get(tag, 0) + 1

    return {
        "total_losses":      len(losses),
        "total_loss_amount": round(total_amount, 2),
        "avg_loss":          round(total_amount / len(losses), 2),
        "loss_tags":         tag_counts,
    }


def get_losing_trades(db_path: str = DB_PATH) -> list[dict]:
    """Return all losing closed_trades ordered by entry_ts DESC."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM closed_trades WHERE (pnl < 0 OR pnl = 0) ORDER BY entry_ts DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def tag_trade(db_path: str, trade_id: str, tag: str, notes: str = "") -> bool:
    """Tag a closed trade with a loss reason. Returns True on success."""
    if tag not in VALID_TAGS:
        raise ValueError(f"Invalid tag '{tag}'. Valid: {sorted(VALID_TAGS)}")
    with _conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE closed_trades SET loss_tag=?, loss_notes=? WHERE id=?",
            (tag, notes, trade_id),
        )
        conn.commit()
        return cur.rowcount > 0


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "scorecard"
    try:
        if mode == "scorecard":
            print(json.dumps(get_scorecard()))
        elif mode == "losses":
            print(json.dumps(get_loss_summary()))
        elif mode == "tag":
            if len(sys.argv) < 4:
                print(json.dumps({"error": "usage: tag <trade_id> <tag> [notes]"}))
                sys.exit(1)
            trade_id = sys.argv[2]
            tag      = sys.argv[3]
            notes    = " ".join(sys.argv[4:]) if len(sys.argv) > 4 else ""
            ok = tag_trade(DB_PATH, trade_id, tag, notes)
            print(json.dumps({"ok": ok, "trade_id": trade_id, "tag": tag}))
        elif mode == "losing_trades":
            print(json.dumps(get_losing_trades()))
        else:
            print(json.dumps({"error": f"unknown mode: {mode}"}))
            sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

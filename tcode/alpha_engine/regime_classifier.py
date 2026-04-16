"""
TSLA Alpha Engine: Intraday Regime Classifier — Phase 16
=========================================================
Combines intelligence signals into a single regime label + strategy recommendation.

Output:
    {
        "regime": "TRENDING",           # TRENDING | FLAT | CHOPPY | EVENT_DRIVEN | UNCERTAIN
        "confidence": 0.78,             # 0.0–1.0
        "factors": [...],               # contributing factor list with weights
        "recommended_strategy": "MOMENTUM",
        "fallback_strategy": "WAVE_RIDER",
        "refreshed_at": "2026-04-16T09:35:00Z",
        "next_refresh_at": "2026-04-16T10:05:00Z"
    }

Refreshes every 30 min during market hours (REFRESH_INTERVAL_MIN), on demand during pre-market.
"""

import json
import os
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# ── constants ─────────────────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "alpha.db")
REFRESH_INTERVAL_MIN = int(os.getenv("REGIME_REFRESH_INTERVAL_MIN", "30"))

# Regime→strategy mapping
_STRATEGY_MAP = {
    "TRENDING":     ("MOMENTUM",     "GAMMA_SCALP"),
    "FLAT":         ("IRON_CONDOR",  "JADE_LIZARD"),
    "CHOPPY":       ("WAVE_RIDER",   "IRON_CONDOR"),
    "EVENT_DRIVEN": ("STRADDLE",     "MOMENTUM"),
    "UNCERTAIN":    ("IRON_CONDOR",  "WAVE_RIDER"),
}

# Color for each regime (used by UI)
_REGIME_COLORS = {
    "TRENDING":     "green",
    "FLAT":         "grey",
    "CHOPPY":       "amber",
    "EVENT_DRIVEN": "blue",
    "UNCERTAIN":    "red",
}


# ── database helpers ──────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Apply schema for new Phase 16 tables if not yet present."""
    schema_path = os.path.join(os.path.dirname(__file__), "data", "schema.sql")
    if os.path.exists(schema_path):
        with open(schema_path) as f:
            conn.executescript(f.read())
        conn.commit()


# ── intelligence data fetching ────────────────────────────────────────────────

def _fetch_intelligence() -> dict:
    """Read latest intelligence snapshot from DB or return empty dict."""
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from heartbeat_query import get_latest_intelligence
        return get_latest_intelligence() or {}
    except Exception:
        pass

    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT raw_json FROM process_heartbeats "
            "WHERE component='intelligence' ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        if row and row["raw_json"]:
            return json.loads(row["raw_json"])
    except Exception:
        pass

    return {}


def _fetch_chop() -> dict:
    """Read latest chop-regime snapshot."""
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT raw_json FROM process_heartbeats "
            "WHERE component IN ('chop_regime','publisher') ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        if row and row["raw_json"]:
            d = json.loads(row["raw_json"])
            return d.get("chop", {})
    except Exception:
        pass
    return {}


# ── factor computation ────────────────────────────────────────────────────────

def _score_pre_market_bias(intel: dict) -> tuple[float, str]:
    """Return (score_contribution, description). Score in [-1, +1]."""
    bias = intel.get("pre_market_bias", {})
    score = float(bias.get("bias", 0.0))
    desc = f"Pre-market bias: {score:+.2f} ({'bullish' if score > 0 else 'bearish' if score < 0 else 'neutral'})"
    # Normalize to [-0.3, +0.3] contribution
    contribution = max(-0.3, min(0.3, score * 0.3))
    return contribution, desc


def _score_macro_regime(intel: dict) -> tuple[float, str]:
    """RISK_ON = +0.2, NEUTRAL = 0, RISK_OFF = -0.2."""
    regime = intel.get("macro_regime", {}).get("regime", "NEUTRAL")
    contrib_map = {"RISK_ON": 0.2, "NEUTRAL": 0.0, "RISK_OFF": -0.2}
    contribution = contrib_map.get(regime, 0.0)
    desc = f"Macro regime: {regime} (VIX={intel.get('macro_regime', {}).get('vix', '?')})"
    return contribution, desc


def _score_vix(intel: dict) -> tuple[float, str]:
    """VIX level: low VIX → trending bias, high VIX → event/uncertain."""
    vix = float(intel.get("macro_regime", {}).get("vix", 20.0))
    if vix < 15:
        contribution = 0.15
        label = "low (trending-friendly)"
    elif vix < 20:
        contribution = 0.05
        label = "normal"
    elif vix < 30:
        contribution = -0.05
        label = "elevated (chop risk)"
    else:
        contribution = -0.15
        label = "high (event/uncertain)"
    desc = f"VIX: {vix:.1f} ({label})"
    return contribution, desc


def _score_correlation_regime(intel: dict) -> tuple[float, str]:
    """IDIOSYNCRATIC = tradeable, MACRO_LOCKED = follow macro."""
    regime = intel.get("correlation_regime", {}).get("regime", "NORMAL")
    contrib_map = {"IDIOSYNCRATIC": 0.1, "NORMAL": 0.0, "MACRO_LOCKED": -0.05}
    contribution = contrib_map.get(regime, 0.0)
    desc = f"Correlation regime: {regime}"
    return contribution, desc


def _score_chop(chop: dict) -> tuple[float, str]:
    """Chop gating: TRENDING = +0.2, MIXED = 0, CHOPPY = -0.3."""
    label = chop.get("regime", "TRENDING")
    score = float(chop.get("score", 0.0))
    contrib_map = {"TRENDING": 0.2, "MIXED": 0.0, "CHOPPY": -0.3}
    contribution = contrib_map.get(label, 0.0)
    desc = f"Intraday chop: {label} (score={score:.2f})"
    return contribution, desc


def _score_ev_sector(intel: dict) -> tuple[float, str]:
    """EV sector momentum."""
    ev = intel.get("ev_sector", {})
    divergence = float(ev.get("tsla_divergence", 0.0))
    if divergence > 0.005:
        contribution = 0.1
        label = "TSLA outperforming EV sector"
    elif divergence < -0.005:
        contribution = -0.1
        label = "TSLA underperforming EV sector"
    else:
        contribution = 0.0
        label = "in-line with EV sector"
    desc = f"EV sector divergence: {divergence:+.3f} ({label})"
    return contribution, desc


def _score_catalyst_risk(catalyst_count: int) -> tuple[float, str]:
    """Catalyst risk: events today push toward EVENT_DRIVEN."""
    if catalyst_count >= 3:
        contribution = -0.2
        label = f"{catalyst_count} high-impact events — event-driven day"
    elif catalyst_count >= 1:
        contribution = -0.1
        label = f"{catalyst_count} event(s) today — elevated catalyst risk"
    else:
        contribution = 0.05
        label = "no catalysts — trend-following conditions"
    desc = f"Catalyst calendar: {label}"
    return contribution, desc


# ── regime determination ──────────────────────────────────────────────────────

def _determine_regime(composite: float, catalyst_count: int, chop_label: str) -> tuple[str, float]:
    """
    Map composite score to regime label + confidence.

    Rules:
    - catalyst_count >= 2 → EVENT_DRIVEN (overrides)
    - chop_label == CHOPPY AND composite < 0.1 → CHOPPY
    - composite >= 0.25 → TRENDING
    - composite >= 0.05 → FLAT
    - composite >= -0.15 → FLAT or CHOPPY
    - composite < -0.15 → UNCERTAIN or CHOPPY
    """
    if catalyst_count >= 2:
        confidence = min(0.9, 0.6 + catalyst_count * 0.1)
        return "EVENT_DRIVEN", confidence

    if chop_label == "CHOPPY" and composite < 0.1:
        confidence = min(0.85, 0.55 + abs(composite) * 0.5)
        return "CHOPPY", confidence

    if composite >= 0.25:
        regime = "TRENDING"
        confidence = min(0.95, 0.65 + composite * 0.8)
    elif composite >= 0.05:
        regime = "FLAT"
        confidence = min(0.80, 0.55 + composite * 0.5)
    elif composite >= -0.10:
        regime = "CHOPPY" if chop_label in ("CHOPPY", "MIXED") else "FLAT"
        confidence = 0.50
    elif composite >= -0.25:
        regime = "CHOPPY"
        confidence = min(0.80, 0.55 + abs(composite) * 0.5)
    else:
        regime = "UNCERTAIN"
        confidence = min(0.75, 0.50 + abs(composite) * 0.3)

    return regime, round(confidence, 2)


# ── catalyst calendar ─────────────────────────────────────────────────────────

def _fetch_catalysts() -> list[dict]:
    """
    Build today's catalyst list. Tries yfinance earnings calendar; falls back to empty.
    Returns list of {name, time, impact, countdown_sec}.
    """
    catalysts: list[dict] = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Check DB for cached catalyst data first
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT raw_json, ts FROM process_heartbeats "
            "WHERE component='catalyst_cache' ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        if row:
            age = time.time() - datetime.fromisoformat(row["ts"].replace("Z", "+00:00")).timestamp()
            if age < 3600:  # 1h cache
                return json.loads(row["raw_json"])
    except Exception:
        pass

    try:
        import yfinance as yf
        tsla = yf.Ticker("TSLA")
        cal = tsla.calendar
        if cal is not None and hasattr(cal, "get"):
            earnings_date = cal.get("Earnings Date")
            if earnings_date and str(earnings_date[0])[:10] == today:
                catalysts.append({
                    "name": "TSLA Earnings",
                    "time": "After Market Close",
                    "impact": "high",
                    "countdown_sec": _seconds_until_market_close(),
                })
    except Exception:
        pass

    # FOMC check (rough heuristic — check for known FOMC dates or env override)
    fomc_date = os.getenv("FOMC_DATE", "")
    if fomc_date == today:
        catalysts.append({
            "name": "FOMC Rate Decision",
            "time": "14:00 ET",
            "impact": "high",
            "countdown_sec": _seconds_until_2pm_et(),
        })

    # Custom catalysts from env (comma-separated JSON strings)
    custom = os.getenv("CUSTOM_CATALYSTS_TODAY", "")
    if custom:
        try:
            catalysts.extend(json.loads(custom))
        except Exception:
            pass

    return catalysts


def _seconds_until_market_close() -> int:
    now_et = datetime.now(timezone.utc).astimezone()
    # Approximate: market closes at 16:00 ET = 20:00 UTC
    close_today = datetime.now(timezone.utc).replace(hour=20, minute=0, second=0, microsecond=0)
    delta = int((close_today - datetime.now(timezone.utc)).total_seconds())
    return max(0, delta)


def _seconds_until_2pm_et() -> int:
    # 14:00 ET ≈ 18:00 UTC (EST) or 19:00 UTC (EDT)
    target = datetime.now(timezone.utc).replace(hour=18, minute=0, second=0, microsecond=0)
    delta = int((target - datetime.now(timezone.utc)).total_seconds())
    return max(0, delta)


# ── main classification ───────────────────────────────────────────────────────

def classify() -> dict:
    """
    Run a full regime classification. Returns the result dict.
    Also persists the result to the process_heartbeats table for caching.
    """
    intel = _fetch_intelligence()
    chop = _fetch_chop()
    catalysts = _fetch_catalysts()
    catalyst_count = sum(1 for c in catalysts if c.get("impact") == "high")
    chop_label = chop.get("regime", "TRENDING")

    # Score factors
    factor_fns = [
        _score_pre_market_bias(intel),
        _score_macro_regime(intel),
        _score_vix(intel),
        _score_correlation_regime(intel),
        _score_chop(chop),
        _score_ev_sector(intel),
        _score_catalyst_risk(catalyst_count),
    ]

    factor_names = [
        "Pre-Market Bias",
        "Macro Regime",
        "VIX Level",
        "Correlation Regime",
        "Intraday Chop",
        "EV Sector Divergence",
        "Catalyst Risk",
    ]

    factors = []
    composite = 0.0
    for (contribution, desc), name in zip(factor_fns, factor_names):
        composite += contribution
        factors.append({
            "name": name,
            "contribution": round(contribution, 3),
            "description": desc,
        })

    regime, confidence = _determine_regime(composite, catalyst_count, chop_label)
    recommended, fallback = _STRATEGY_MAP.get(regime, ("IRON_CONDOR", "WAVE_RIDER"))

    now = datetime.now(timezone.utc)
    next_refresh = now + timedelta(minutes=REFRESH_INTERVAL_MIN)

    result = {
        "regime": regime,
        "color": _REGIME_COLORS.get(regime, "grey"),
        "confidence": confidence,
        "composite_score": round(composite, 3),
        "factors": factors,
        "recommended_strategy": recommended,
        "fallback_strategy": fallback,
        "catalysts": catalysts,
        "refreshed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "next_refresh_at": next_refresh.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # Persist to heartbeats for caching
    try:
        conn = _get_db()
        _ensure_tables(conn)
        conn.execute(
            "INSERT INTO process_heartbeats (component, ts, status, detail) VALUES (?, ?, ?, ?)",
            ("regime_classifier", now.strftime("%Y-%m-%dT%H:%M:%SZ"), "ok", json.dumps(result)),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("Could not persist regime classification: %s", e)

    return result


def get_cached() -> Optional[dict]:
    """Return the most recent cached classification (up to REFRESH_INTERVAL_MIN old)."""
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT detail, ts FROM process_heartbeats "
            "WHERE component='regime_classifier' ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row and row["detail"]:
            data = json.loads(row["detail"])
            # Check staleness
            ts = datetime.fromisoformat(data.get("refreshed_at", "2000-01-01T00:00:00Z").replace("Z", "+00:00"))
            age_min = (datetime.now(timezone.utc) - ts).total_seconds() / 60
            if age_min < REFRESH_INTERVAL_MIN:
                return data
    except Exception:
        pass
    return None


def get_or_refresh() -> dict:
    """Return cached if fresh, otherwise recompute."""
    cached = get_cached()
    if cached:
        return cached
    return classify()


# ── CLI interface ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    if force:
        result = classify()
    else:
        result = get_or_refresh()
    print(json.dumps(result, indent=2))

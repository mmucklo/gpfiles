"""
Alpha Engine: Per-Archetype Trading Parameters
===============================================
Each archetype maps to a distinct trading strategy with its own:
  - delta: target option delta (0-1 for calls, absolute value for puts)
  - risk_pct: fraction of NOTIONAL_ACCOUNT_SIZE risked per trade
  - rr: reward-to-risk ratio (take_profit = entry + rr * (entry - stop_loss))
  - expiry: DTE string resolved by compute_expiry()
  - greeks_profile: Phase 14 strike-selection Greeks targets (see GreeksProfile below)

These parameters drive all sizing and strike-selection logic in publisher.py.
No hardcoded dollar amounts — all derived from NOTIONAL_ACCOUNT_SIZE.

Versioned in git. Add a comment with the date when tuning any parameter.

Chop gating (Phase 14):
  CHOPPY regime:
    DIRECTIONAL_STRONG/STD/MEAN_REVERT: skip emission [CHOP-BLOCK]
    SCALP_0DTE: skip emission [CHOP-BLOCK]
    VOL_PLAY: skip if rv_iv_ratio < 0.7 (IV too rich); otherwise allow
  MIXED regime:
    DIRECTIONAL_STRONG/STD/MEAN_REVERT: confidence × 0.7 [CHOP-DOWNWEIGHT]
    SCALP_0DTE: confidence × 0.6 [CHOP-DOWNWEIGHT]
    VOL_PLAY: confidence × 1.1 (vega benefits from compression) [CHOP-BOOST]
  TRENDING: no adjustment
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class GreeksProfile:
    """
    Per-archetype Greeks targets for Phase 14 strike selection.

    Calibrated 2026-04-13; revisit after 30 trading days of attribution data
    to measure whether greeks-selection outperforms moneyness-only.

    Fields:
        target_delta_abs        |delta| we want (e.g. 0.30)
        delta_tolerance         acceptable band (e.g. 0.05 → [0.25, 0.35])
        max_theta_pct_premium   reject if |theta|/premium > this (theta burn cap)
        min_vega_for_vol_play   only enforced for VOL_PLAY archetype
        prefer_ttm_days         (min, max) days to expiry preference
    """
    target_delta_abs: float
    delta_tolerance: float
    max_theta_pct_premium: float
    min_vega_for_vol_play: float
    prefer_ttm_days: tuple  # (min_dte, max_dte)


# Per-archetype GreeksProfiles
# Calibrated 2026-04-13; revisit after 30 trading days of attribution data.
GREEKS_PROFILES: dict[str, GreeksProfile] = {
    "DIRECTIONAL_STRONG": GreeksProfile(
        target_delta_abs=0.40,
        delta_tolerance=0.07,
        max_theta_pct_premium=0.06,   # 6% daily theta burn cap
        min_vega_for_vol_play=0.0,    # N/A for directional
        prefer_ttm_days=(5, 14),
    ),
    "DIRECTIONAL_STD": GreeksProfile(
        target_delta_abs=0.30,
        delta_tolerance=0.05,
        max_theta_pct_premium=0.05,   # 5% daily theta burn cap
        min_vega_for_vol_play=0.0,
        prefer_ttm_days=(7, 21),
    ),
    "MEAN_REVERT": GreeksProfile(
        target_delta_abs=0.35,
        delta_tolerance=0.05,
        max_theta_pct_premium=0.05,
        min_vega_for_vol_play=0.0,
        prefer_ttm_days=(5, 10),
    ),
    "SCALP_0DTE": GreeksProfile(
        target_delta_abs=0.50,
        delta_tolerance=0.10,
        max_theta_pct_premium=0.25,   # 25% — 0DTE is theta-intensive by design
        min_vega_for_vol_play=0.0,
        prefer_ttm_days=(0, 1),
    ),
    "VOL_PLAY": GreeksProfile(
        target_delta_abs=0.50,
        delta_tolerance=0.10,
        max_theta_pct_premium=0.04,   # 4% — long vega, want low theta burn
        min_vega_for_vol_play=0.10,   # require vega >= 0.10 per contract
        prefer_ttm_days=(21, 45),     # long vega benefits from longer duration
    ),
}


ARCHETYPES: dict[str, dict] = {
    "DIRECTIONAL_STRONG": {
        # High-conviction directional: ATM, wide stop
        "delta": 0.45,
        "risk_pct": 0.015,   # 1.5% of notional
        "rr": 3.0,
        "expiry": "7DTE",
    },
    "DIRECTIONAL_STD": {
        # Standard directional: 20-30 delta, moderate risk
        "delta": 0.25,
        "risk_pct": 0.010,   # 1.0% of notional
        "rr": 2.5,
        "expiry": "7DTE",
    },
    "MEAN_REVERT": {
        # Mean-reversion scalp: slightly ITM, tight duration
        "delta": 0.55,
        "risk_pct": 0.0075,  # 0.75% of notional
        "rr": 1.0,
        "expiry": "2DTE",
    },
    "SCALP_0DTE": {
        # 0DTE intraday scalp: ATM+, tiny risk per contract
        "delta": 0.55,
        "risk_pct": 0.0025,  # 0.25% of notional
        "rr": 1.0,
        "expiry": "0DTE",
    },
    "VOL_PLAY": {
        # Volatility expansion/compression: far OTM wings
        "delta": 0.18,
        "risk_pct": 0.010,   # 1.0% of notional
        "rr": 2.0,
        "expiry": "30DTE",
    },
}

# Required keys — every archetype must supply all of these.
REQUIRED_FIELDS = {"delta", "risk_pct", "rr", "expiry"}

# Model-type → archetype mapping.
# These drive which archetype parameters are applied to each signal model.
MODEL_ARCHETYPE_MAP: dict[str, str] = {
    "SENTIMENT":    "DIRECTIONAL_STD",
    "OPTIONS_FLOW": "DIRECTIONAL_STRONG",
    "MACRO":        "DIRECTIONAL_STD",
    "VOLATILITY":   "VOL_PLAY",
    "CONTRARIAN":   "MEAN_REVERT",
    "EV_SECTOR":    "DIRECTIONAL_STD",
    "PREMARKET":    "DIRECTIONAL_STD",
}

_FALLBACK_ARCHETYPE = "DIRECTIONAL_STD"


def get_archetype(model_name: str) -> dict:
    """Return the archetype config for a given model name, with fallback."""
    key = MODEL_ARCHETYPE_MAP.get(model_name, _FALLBACK_ARCHETYPE)
    return ARCHETYPES[key]


def get_greeks_profile(archetype_name: str) -> Optional[GreeksProfile]:
    """Return the GreeksProfile for a named archetype, or None if unknown."""
    return GREEKS_PROFILES.get(archetype_name)


def validate_archetypes() -> list[str]:
    """Return a list of validation errors. Empty list = all valid."""
    errors: list[str] = []
    for name, cfg in ARCHETYPES.items():
        for field in REQUIRED_FIELDS:
            if field not in cfg:
                errors.append(f"{name}: missing required field '{field}'")
        if "delta" in cfg and not (0 < cfg["delta"] < 1):
            errors.append(f"{name}: delta={cfg['delta']} must be between 0 and 1 exclusive")
        if "risk_pct" in cfg and not (0 < cfg["risk_pct"] <= 0.05):
            errors.append(f"{name}: risk_pct={cfg['risk_pct']} suspiciously large (>5%)")
        if "rr" in cfg and cfg["rr"] <= 0:
            errors.append(f"{name}: rr={cfg['rr']} must be positive")
    return errors


def compute_regime_kelly(
    confidence: float,
    vix: float,
    regime: str,
    realized_vol: float,
    implied_vol: float,
) -> tuple[float, dict]:
    """
    Regime-conditional Kelly sizing formula.

    Combined formula:
        final_risk_pct = archetype.risk_pct × kelly_base_fraction × vol_ratio × regime_multiplier
        capped at 0.02 (2% of NOTIONAL_ACCOUNT_SIZE, Phase 10 hard cap)

    Kelly wager fraction:
        full_kelly   = max(0, 2 × confidence − 1)   [binary-bet approximation, Thorp 2006]
        kelly_wager  = full_kelly × final_multiplier

    VIX-tiered base fraction (quarter-Kelly staircase):
        VIX > 30  → 0.20   HIGH_VIX: 1/5 Kelly
        VIX > 20  → 0.35   MED_VIX
        VIX ≤ 20  → 0.50   LOW_VIX: half Kelly max

    Vol-targeting (AQR 2012):
        vol_ratio = min(1.0, realized_vol / implied_vol)
        If IV > realized → options "rich" → size down proportionally

    Regime multiplier:
        RISK_OFF → 0.5 (halve position regardless of VIX)
        else     → 1.0

    Returns:
        (kelly_wager_pct, audit_dict)  — audit_dict contains all intermediate values
        for fills_audit table insertion.
    """
    full_kelly = max(0.0, 2 * confidence - 1)

    if vix > 30:
        kelly_base_fraction = 0.20
    elif vix > 20:
        kelly_base_fraction = 0.35
    else:
        kelly_base_fraction = 0.50

    if implied_vol > 0 and realized_vol > 0:
        vol_ratio = min(1.0, realized_vol / implied_vol)
    else:
        vol_ratio = 1.0

    regime_multiplier = 0.5 if regime == "RISK_OFF" else 1.0
    final_multiplier = kelly_base_fraction * vol_ratio * regime_multiplier
    kelly_wager_pct = full_kelly * final_multiplier

    audit = {
        "regime": regime,
        "vix": vix,
        "kelly_base_fraction": kelly_base_fraction,
        "vol_ratio": vol_ratio,
        "regime_multiplier": regime_multiplier,
        "final_multiplier": final_multiplier,
    }
    return kelly_wager_pct, audit


if __name__ == "__main__":
    errs = validate_archetypes()
    if errs:
        print("VALIDATION ERRORS:")
        for e in errs:
            print(f"  {e}")
    else:
        print(f"All {len(ARCHETYPES)} archetypes valid.")
        for name, cfg in ARCHETYPES.items():
            print(f"  {name}: delta={cfg['delta']:.2f} risk={cfg['risk_pct']*100:.2f}% "
                  f"rr={cfg['rr']}:1 expiry={cfg['expiry']}")

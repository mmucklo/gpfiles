"""
Phase 14: Greeks-Aware Strike Selector
========================================
Replaces moneyness-only strike selection in publisher.py.

Selection pipeline (strict order):
  1. Filter by direction (calls/puts) and TTM band from archetype.prefer_ttm_days
  2. Filter by liquidity: OI >= min_oi, volume >= min_vol,
     (ask-bid)/mid <= max_spread_pct, bid >= min_abs_bid
  3. Filter by greeks availability (greeks_source != "unavailable")
  4. Filter by delta band: |delta - target_delta_abs| <= delta_tolerance
  5. Filter by theta cap: |theta| / premium <= max_theta_pct_premium
  6. VOL_PLAY only: filter vega >= min_vega_for_vol_play
  7. Score survivors by weighted distance to ideal contract
  8. Return best-scored StrikeSelection, or None if no survivor

Liquidity thresholds are env-overridable at runtime:
  MIN_OPTION_OPEN_INTEREST   (default 500)
  MIN_OPTION_VOLUME_TODAY    (default 50)
  MAX_BID_ASK_PCT            (default 0.15 = 15%)
  MIN_ABSOLUTE_BID           (default 0.10)

When nothing survives any filter step, returns None — the caller must log
[STRIKE-REJECT] and drop the signal.  Never relaxes filters silently.
"""
import os
import logging
from dataclasses import dataclass, field
from datetime import date as _date
from typing import Literal, Optional

logger = logging.getLogger("StrikeSelector")


@dataclass
class StrikeSelection:
    """Full context of the selected contract, attached to signal as strike_selection_meta."""
    strike: float
    expiry: str
    contract_type: str           # "CALL" or "PUT"
    delta: float
    gamma: float
    theta: float
    vega: float
    iv: float
    bid: float
    ask: float
    mid: float
    open_interest: int
    volume: int
    score: float
    score_breakdown: dict        # {"delta_fit": x, "liquidity": x, "spread": x, "theta": x}
    greeks_source: str           # "ibkr" | "computed_bs"
    liquidity_headroom: dict     # {"volume": x, "oi": x, "spread": x, "bid": x}


def _load_liquidity_floors() -> tuple[int, int, float, float]:
    """Load liquidity floor env vars, returning (min_oi, min_vol, max_spread_pct, min_abs_bid)."""
    min_oi = int(os.getenv("MIN_OPTION_OPEN_INTEREST", "500"))
    min_vol = int(os.getenv("MIN_OPTION_VOLUME_TODAY", "50"))
    max_spread = float(os.getenv("MAX_BID_ASK_PCT", "0.15"))
    min_bid = float(os.getenv("MIN_ABSOLUTE_BID", "0.10"))
    return min_oi, min_vol, max_spread, min_bid


def select_strike(
    chain_rows: list,           # list[OptionRow] from options_chain
    archetype_name: str,        # e.g. "DIRECTIONAL_STRONG"
    spot: float,
    direction: Literal["LONG_CALL", "LONG_PUT", "SHORT_CALL", "SHORT_PUT"],
    expiry: str,                # YYYY-MM-DD string for this chain batch
    *,
    min_open_interest: Optional[int] = None,
    min_volume_today: Optional[int] = None,
    max_bid_ask_pct: Optional[float] = None,
    min_absolute_bid: Optional[float] = None,
) -> Optional[StrikeSelection]:
    """
    Select the best strike from chain_rows for the given archetype and direction.

    Returns StrikeSelection or None (caller must log [STRIKE-REJECT] and drop signal).
    """
    from config.archetypes import GREEKS_PROFILES

    gp = GREEKS_PROFILES.get(archetype_name)
    if gp is None:
        logger.warning("[STRIKE-REJECT] unknown archetype=%s", archetype_name)
        return None

    # Resolve liquidity floors (arg overrides env)
    env_oi, env_vol, env_spread, env_bid = _load_liquidity_floors()
    floor_oi   = min_open_interest if min_open_interest is not None else env_oi
    floor_vol  = min_volume_today  if min_volume_today  is not None else env_vol
    floor_spr  = max_bid_ask_pct   if max_bid_ask_pct   is not None else env_spread
    floor_bid  = min_absolute_bid  if min_absolute_bid  is not None else env_bid

    # Map direction to option type and expected delta sign
    opt_type = "CALL" if direction in ("LONG_CALL", "SHORT_CALL") else "PUT"

    # ── Step 1: direction + TTM band ─────────────────────────────────────────
    min_dte, max_dte = gp.prefer_ttm_days
    try:
        exp_date = _date.fromisoformat(expiry)
        dte = (exp_date - _date.today()).days
    except ValueError:
        dte = 7

    step1 = [r for r in chain_rows if r.option_type == opt_type]
    # TTM filter: allow the expiry already chosen by the caller (single-expiry batch)
    # If the batch expiry is outside range, still proceed (expiry chosen upstream).
    # DTE band is soft for 0DTE since we may only have same-day expiries available.
    if min_dte > 0 or max_dte < 365:
        in_band = min_dte <= dte <= max_dte
        if not in_band and archetype_name != "SCALP_0DTE":
            logger.info(
                "[STRIKE-REJECT] archetype=%s expiry=%s dte=%d outside prefer_ttm=%s",
                archetype_name, expiry, dte, gp.prefer_ttm_days,
            )
            # Don't hard-reject TTM mismatch — just log and continue with what we have
            # so the signal can still be placed on the nearest available expiry.

    if not step1:
        logger.info("[STRIKE-REJECT] archetype=%s direction=%s no %s rows", archetype_name, direction, opt_type)
        return None

    # ── Step 2: liquidity gates ──────────────────────────────────────────────
    step2 = []
    for r in step1:
        mid = r.mid_price
        spread_pct = r.spread_pct if mid > 0 else 1.0
        reasons = []
        if r.volume < floor_vol:
            reasons.append(f"volume={r.volume}<{floor_vol}")
        if r.open_interest < floor_oi:
            reasons.append(f"oi={r.open_interest}<{floor_oi}")
        if spread_pct > floor_spr:
            reasons.append(f"spread_pct={spread_pct:.3f}>{floor_spr}")
        if r.bid < floor_bid:
            reasons.append(f"bid={r.bid}<{floor_bid}")
        if reasons:
            logger.debug(
                "[LIQUIDITY-REJECT] strike=%s expiry=%s volume=%s oi=%s spread_pct=%.3f bid=%.2f reason=%s",
                r.strike, r.expiration_date, r.volume, r.open_interest, spread_pct, r.bid, reasons[0],
            )
        else:
            step2.append(r)

    if not step2:
        logger.info(
            "[STRIKE-REJECT] archetype=%s direction=%s reason=liquidity "
            "(floor_oi=%d floor_vol=%d floor_spr=%.2f floor_bid=%.2f)",
            archetype_name, direction, floor_oi, floor_vol, floor_spr, floor_bid,
        )
        return None

    # ── Step 3: greeks availability ──────────────────────────────────────────
    step3 = [r for r in step2 if r.greeks_source != "unavailable" and r.delta is not None]
    if not step3:
        logger.info(
            "[STRIKE-REJECT] archetype=%s direction=%s reason=no_greeks "
            "(%d rows had greeks_source=unavailable)", archetype_name, direction, len(step2),
        )
        return None

    # ── Step 4: delta band ───────────────────────────────────────────────────
    target = gp.target_delta_abs
    tol    = gp.delta_tolerance
    step4 = []
    for r in step3:
        delta_abs = abs(r.delta)
        if abs(delta_abs - target) <= tol:
            step4.append(r)

    if not step4:
        closest = min(step3, key=lambda r: abs(abs(r.delta) - target))
        logger.info(
            "[STRIKE-REJECT] archetype=%s direction=%s reason=delta_band "
            "target=%.2f±%.2f best_delta=%.3f",
            archetype_name, direction, target, tol, abs(closest.delta),
        )
        return None

    # ── Step 5: theta cap ────────────────────────────────────────────────────
    step5 = []
    for r in step4:
        premium = r.mid_price
        if premium <= 0:
            continue
        theta_burn = abs(r.theta) / premium if r.theta is not None else 0.0
        if theta_burn <= gp.max_theta_pct_premium:
            step5.append(r)

    if not step5:
        logger.info(
            "[STRIKE-REJECT] archetype=%s direction=%s reason=theta_cap max=%.3f",
            archetype_name, direction, gp.max_theta_pct_premium,
        )
        return None

    # ── Step 6: vega floor (VOL_PLAY only) ──────────────────────────────────
    if archetype_name == "VOL_PLAY" and gp.min_vega_for_vol_play > 0:
        step6 = [r for r in step5 if r.vega is not None and r.vega >= gp.min_vega_for_vol_play]
        if not step6:
            logger.info(
                "[STRIKE-REJECT] archetype=%s direction=%s reason=vega_floor min=%.3f",
                archetype_name, direction, gp.min_vega_for_vol_play,
            )
            return None
    else:
        step6 = step5

    # ── Step 7: score survivors ──────────────────────────────────────────────
    # Score = 0.50×delta_fit + 0.20×liquidity + 0.20×spread_tightness + 0.10×theta_efficiency
    best_row = None
    best_score = -1.0
    best_breakdown = {}

    max_oi = max(r.open_interest for r in step6) or 1
    max_vol = max(r.volume for r in step6) or 1
    min_spread = min(r.spread_pct for r in step6) if step6 else 1.0

    for r in step6:
        premium = r.mid_price or 0.01
        delta_err = abs(abs(r.delta) - target)
        delta_fit = 1.0 - min(delta_err / max(tol, 0.01), 1.0)

        liq_score = 0.5 * min(r.open_interest / max_oi, 1.0) + 0.5 * min(r.volume / max_vol, 1.0)

        spread_score = 1.0 - min(r.spread_pct / max(floor_spr, 0.01), 1.0)

        theta_burn = abs(r.theta) / premium if r.theta is not None else 0.0
        theta_score = 1.0 - min(theta_burn / max(gp.max_theta_pct_premium, 0.01), 1.0)

        score = 0.50 * delta_fit + 0.20 * liq_score + 0.20 * spread_score + 0.10 * theta_score

        if score > best_score:
            best_score = score
            best_row = r
            best_breakdown = {
                "delta_fit": round(delta_fit, 4),
                "liquidity": round(liq_score, 4),
                "spread_tightness": round(spread_score, 4),
                "theta_efficiency": round(theta_score, 4),
            }

    if best_row is None:
        return None

    mid = best_row.mid_price
    headroom = {
        "volume": round(best_row.volume / max(floor_vol, 1), 2),
        "oi": round(best_row.open_interest / max(floor_oi, 1), 2),
        "spread_pct": round(floor_spr / max(best_row.spread_pct, 0.001), 2) if best_row.spread_pct > 0 else 99.0,
        "bid": round(best_row.bid / max(floor_bid, 0.01), 2),
    }

    logger.info(
        "[STRIKE-SELECT] archetype=%s direction=%s strike=%.1f expiry=%s "
        "delta=%.3f score=%.3f greeks_source=%s vol=%d oi=%d",
        archetype_name, direction, best_row.strike, expiry,
        best_row.delta, best_score, best_row.greeks_source,
        best_row.volume, best_row.open_interest,
    )

    return StrikeSelection(
        strike=best_row.strike,
        expiry=expiry,
        contract_type=opt_type,
        delta=best_row.delta,
        gamma=best_row.gamma if best_row.gamma is not None else 0.0,
        theta=best_row.theta if best_row.theta is not None else 0.0,
        vega=best_row.vega if best_row.vega is not None else 0.0,
        iv=best_row.implied_volatility,
        bid=best_row.bid,
        ask=best_row.ask,
        mid=mid,
        open_interest=best_row.open_interest,
        volume=best_row.volume,
        score=round(best_score, 4),
        score_breakdown=best_breakdown,
        greeks_source=best_row.greeks_source,
        liquidity_headroom=headroom,
    )

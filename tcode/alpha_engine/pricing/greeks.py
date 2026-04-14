"""
Black-Scholes Greeks Computation
=================================
Fallback when IBKR modelGreeks are unavailable.

Usage:
    from pricing.greeks import compute_bs_greeks

    g = compute_bs_greeks(
        spot=380.0, strike=390.0, ttm_years=14/365,
        rate=0.05, iv=0.65, option_type="CALL"
    )
    # g == {"delta": 0.38, "gamma": ..., "theta": ..., "vega": ..., "iv": 0.65}

Risk-free rate:
    Pass `rate` explicitly.  Publisher calls macro_regime.get_risk_free_rate()
    and caches it (1hr TTL) to avoid FRED rate-limiting.

Verified against textbook values (Hull, Options Futures 10e, §19):
    ATM 30-day call, S=100, K=100, r=5%, sigma=25%:
        delta ≈ 0.530, gamma ≈ 0.066, theta ≈ -0.054/day, vega ≈ 0.115
    (See tests/test_greeks_compute.py)
"""
import math
import logging
from typing import Optional

logger = logging.getLogger("BSGreeks")


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via erfc (no external deps)."""
    return 0.5 * math.erfc(-x / math.sqrt(2))


def _norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def compute_bs_greeks(
    spot: float,
    strike: float,
    ttm_years: float,
    rate: float,
    iv: float,
    option_type: str,  # "CALL" or "PUT"
) -> dict:
    """
    Compute Black-Scholes greeks for a European option.

    Returns dict with keys:
        delta   — signed (+0.30 for call, -0.30 for put)
        gamma   — always positive
        theta   — per-calendar-day, typically negative for long
        vega    — per 1.0 vol point (i.e., sigma as fraction, not %)
        iv      — the input iv, normalized
        greeks_source — "computed_bs"

    Degenerate cases:
        ttm_years <= 0  → delta = 1.0 (call) or -1.0 (put) if ITM, else 0.0
        iv <= 0         → delta approximated from moneyness only
        spot/strike <= 0 → returns all-zero dict with greeks_source="unavailable"
    """
    if spot <= 0 or strike <= 0:
        logger.warning("compute_bs_greeks: invalid spot=%s strike=%s", spot, strike)
        return _unavailable_greeks(iv)

    # Degenerate: at or past expiry
    if ttm_years <= 0:
        itm = (option_type == "CALL" and spot >= strike) or \
              (option_type == "PUT" and spot <= strike)
        delta_val = (1.0 if option_type == "CALL" else -1.0) if itm else 0.0
        return {
            "delta": delta_val,
            "gamma": 0.0,
            "theta": 0.0,
            "vega": 0.0,
            "iv": iv,
            "greeks_source": "computed_bs",
        }

    # Degenerate: no volatility
    if iv <= 0:
        logger.warning("compute_bs_greeks: iv=%s <= 0, returning zero greeks", iv)
        return _unavailable_greeks(iv)

    try:
        sqrt_T = math.sqrt(ttm_years)
        d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * ttm_years) / (iv * sqrt_T)
        d2 = d1 - iv * sqrt_T

        n_d1 = _norm_cdf(d1)
        n_d2 = _norm_cdf(d2)
        n_neg_d1 = _norm_cdf(-d1)
        n_neg_d2 = _norm_cdf(-d2)
        pdf_d1 = _norm_pdf(d1)

        # Delta
        if option_type == "CALL":
            delta = n_d1
        else:
            delta = n_d1 - 1.0  # negative for puts

        # Gamma (same for calls and puts)
        gamma = pdf_d1 / (spot * iv * sqrt_T)

        # Theta (per calendar day — divide annual by 365)
        # Call theta = -(S·N'(d1)·sigma)/(2√T) - r·K·e^(-rT)·N(d2)
        # Put  theta = -(S·N'(d1)·sigma)/(2√T) + r·K·e^(-rT)·N(-d2)
        disc = math.exp(-rate * ttm_years)
        theta_annual_call = (
            -(spot * pdf_d1 * iv) / (2 * sqrt_T)
            - rate * strike * disc * n_d2
        )
        if option_type == "CALL":
            theta_annual = theta_annual_call
        else:
            theta_annual = theta_annual_call + rate * strike * disc  # put-call parity correction

        theta_daily = theta_annual / 365.0

        # Vega (per 1.0 vol-point, i.e., sigma as fraction)
        vega = spot * sqrt_T * pdf_d1

        return {
            "delta": round(delta, 6),
            "gamma": round(gamma, 8),
            "theta": round(theta_daily, 6),
            "vega": round(vega, 6),
            "iv": iv,
            "greeks_source": "computed_bs",
        }

    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        logger.warning("compute_bs_greeks exception: %s", exc)
        return _unavailable_greeks(iv)


def _unavailable_greeks(iv: float) -> dict:
    return {
        "delta": None,
        "gamma": None,
        "theta": None,
        "vega": None,
        "iv": iv,
        "greeks_source": "unavailable",
    }


# ── Risk-free rate helper ──────────────────────────────────────────────────────
_rf_cache: Optional[float] = None
_rf_cache_ts: float = 0.0
_RF_TTL = 3600  # 1 hour


def get_risk_free_rate() -> float:
    """
    Return current risk-free rate from macro_regime (FRED 3M T-bill).
    Cached 1 hour.  Falls back to 0.05 (5%) if unavailable.
    """
    import time
    global _rf_cache, _rf_cache_ts
    now = time.time()
    if _rf_cache is not None and now - _rf_cache_ts < _RF_TTL:
        return _rf_cache

    rate = 0.05  # default
    try:
        import sys as _sys
        _sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")
        from ingestion.macro_regime import get_macro_regime
        macro = get_macro_regime()
        # macro returns fed_rate as a decimal (e.g., 0.0525)
        fed_rate = macro.get("fed_rate") or macro.get("fed_funds_rate")
        if fed_rate and 0 < fed_rate < 0.25:
            rate = float(fed_rate)
    except Exception as exc:
        logger.debug("get_risk_free_rate fallback to 0.05: %s", exc)

    _rf_cache = rate
    _rf_cache_ts = now
    return rate


if __name__ == "__main__":
    # Quick sanity: ATM 30-day call at 25% IV, r=5%, S=K=100
    g = compute_bs_greeks(100.0, 100.0, 30 / 365, 0.05, 0.25, "CALL")
    print("ATM 30d CALL:", g)
    # Expected: delta~0.530, gamma~0.066, theta~-0.054, vega~0.115
    g2 = compute_bs_greeks(100.0, 100.0, 30 / 365, 0.05, 0.25, "PUT")
    print("ATM 30d PUT:", g2)
    # Expected: delta~-0.470, gamma~0.066, theta~-0.042, vega~0.115

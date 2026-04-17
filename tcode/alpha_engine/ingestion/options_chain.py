"""
TSLA Alpha Engine: Real-Time Options Chain Ingestion
Fetches the TSLA options chain and provides strike selection anchored to real
market data with liquidity filtering.

Source priority (controlled by OPTIONS_CHAIN_SOURCE env var):
  - "tradier" (default / "auto"): Tradier real-time chain with native greeks
  - "yfinance": yfinance fallback (no native greeks)
  - "ibkr": IBKR paper account (requires OPRA subscription)
  - "auto": Tradier → yfinance → IBKR cascade

Cache TTL: 60s — balances freshness vs. rate-limit safety.
"""
import os
import time
import logging
from dataclasses import dataclass, field
from typing import Optional
import yfinance as yf

logger = logging.getLogger("OptionsChain")

try:
    import sys as _sys
    _sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")
    from heartbeat import emit_heartbeat as _hb
except Exception:
    def _hb(component, status="ok", detail=None, **_kw): pass  # type: ignore


# ── market-hours helper ───────────────────────────────────────────────────────

def _is_us_market_hours() -> bool:
    """Return True if current time is within US market hours (9:30–16:00 ET Mon–Fri)."""
    import datetime
    import zoneinfo
    try:
        tz = zoneinfo.ZoneInfo("America/New_York")
    except Exception:
        return True  # If we can't determine tz, default to "in-hours" (safer)
    now = datetime.datetime.now(tz)
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    t = now.hour * 60 + now.minute
    return 570 <= t < 960  # 9:30 (570) – 16:00 (960)


def round_to_chain_increment(price: float, increment: float = 5.0) -> float:
    """Round a price to the nearest standard chain increment."""
    return round(price / increment) * increment


@dataclass
class OptionRow:
    strike: float
    option_type: str          # "CALL" or "PUT"
    expiration_date: str      # YYYY-MM-DD
    implied_volatility: float # annualised, e.g. 0.65 = 65%
    open_interest: int
    bid: float
    ask: float
    last_price: float
    volume: int = 0           # contracts traded today

    # Greeks — populated by enrich_greeks()
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    greeks_source: str = "unavailable"  # "ibkr" | "computed_bs" | "unavailable"

    @property
    def mid_price(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.last_price

    @property
    def spread_pct(self) -> float:
        """Bid/ask spread as a fraction of mid. Low = liquid."""
        mid = self.mid_price
        if mid <= 0:
            return 1.0
        return (self.ask - self.bid) / mid


def _enrich_greeks_vectorized(rows, spot, ttm_years, rate):
    """
    Vectorized Black-Scholes greeks using numpy (~10x faster than per-row loop).
    Mutates rows in-place.  Rows with greeks_source=="ibkr" are skipped.
    Returns number of unavailable rows.
    """
    import numpy as np

    # Split into numpy-eligible vs already-set
    eligible = [r for r in rows if r.greeks_source != "ibkr" and r.implied_volatility and r.implied_volatility > 0]
    unavail_rows = [r for r in rows if r.greeks_source != "ibkr" and not (r.implied_volatility and r.implied_volatility > 0)]

    for r in unavail_rows:
        r.greeks_source = "unavailable"

    if not eligible:
        return len(unavail_rows)

    K = np.array([r.strike for r in eligible], dtype=np.float64)
    iv = np.array([r.implied_volatility for r in eligible], dtype=np.float64)
    is_put = np.array([r.option_type == "PUT" for r in eligible], dtype=bool)

    sqrt_T = np.sqrt(ttm_years) if ttm_years > 0 else 0.0

    if ttm_years <= 0 or sqrt_T == 0:
        # At/past expiry: delta is intrinsic, other greeks zero
        for i, r in enumerate(eligible):
            itm = (not is_put[i] and spot >= K[i]) or (is_put[i] and spot <= K[i])
            r.delta = (1.0 if not is_put[i] else -1.0) if itm else 0.0
            r.gamma = 0.0
            r.theta = 0.0
            r.vega = 0.0
            r.greeks_source = "computed_bs"
        return len(unavail_rows)

    S = float(spot)
    r_rate = float(rate)

    d1 = (np.log(S / K) + (r_rate + 0.5 * iv * iv) * ttm_years) / (iv * sqrt_T)
    d2 = d1 - iv * sqrt_T

    # Vectorized normal CDF/PDF
    from scipy.special import ndtr as _ndtr  # type: ignore
    N_d1 = _ndtr(d1)
    N_d2 = _ndtr(d2)
    pdf_d1 = np.exp(-0.5 * d1 * d1) / np.sqrt(2.0 * np.pi)

    delta = np.where(is_put, N_d1 - 1.0, N_d1)
    gamma = pdf_d1 / (S * iv * sqrt_T)
    disc = np.exp(-r_rate * ttm_years)
    theta_call = -(S * pdf_d1 * iv) / (2 * sqrt_T) - r_rate * K * disc * N_d2
    theta = np.where(is_put, theta_call + r_rate * K * disc, theta_call) / 365.0
    vega = S * sqrt_T * pdf_d1

    for i, r in enumerate(eligible):
        r.delta = float(round(delta[i], 6))
        r.gamma = float(round(gamma[i], 8))
        r.theta = float(round(theta[i], 6))
        r.vega = float(round(vega[i], 6))
        r.greeks_source = "computed_bs"

    return len(unavail_rows)


def enrich_greeks(rows: list["OptionRow"], spot: float, ttm_years: float) -> None:
    """
    Mutate each OptionRow in-place: fill delta/gamma/theta/vega/greeks_source.

    Priority:
      1. IBKR modelGreeks already set on the row (greeks_source == "ibkr") — skip.
      2. BS-compute from IV when IV > 0.  Vectorized via numpy+scipy when available;
         falls back to per-row scalar loop.
      3. Mark greeks_source="unavailable" and leave None values.

    Also tracks degradation: logs WARNING if >50% of rows end up unavailable.
    """
    try:
        import sys as _sys
        _sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")
        from pricing.greeks import get_risk_free_rate
        rate = get_risk_free_rate()
    except Exception as exc:
        logger.warning("enrich_greeks: cannot import greeks module: %s", exc)
        return

    # Try vectorized path first (numpy+scipy available)
    try:
        unavail_count = _enrich_greeks_vectorized(rows, spot, ttm_years, rate)
        total = len(rows)
        if total > 0 and unavail_count / total > 0.5:
            logger.warning(
                "Greeks data degraded: %d/%d strikes missing greeks. "
                "Strike selector will skip unavailable rows.",
                unavail_count, total,
            )
        return
    except ImportError:
        logger.debug("enrich_greeks: numpy/scipy unavailable, falling back to scalar loop")
    except Exception as exc:
        logger.warning("enrich_greeks: vectorized path failed (%s), falling back to scalar loop", exc)

    # Scalar fallback: per-row loop
    try:
        from pricing.greeks import compute_bs_greeks
    except Exception as exc:
        logger.warning("enrich_greeks: cannot import compute_bs_greeks: %s", exc)
        return

    unavail_count = 0
    for row in rows:
        if row.greeks_source == "ibkr":
            # Already populated by IBKR modelGreeks — trust and skip
            continue
        iv = row.implied_volatility
        if iv and iv > 0:
            try:
                g = compute_bs_greeks(spot, row.strike, ttm_years, rate, iv, row.option_type)
                row.delta = g["delta"]
                row.gamma = g["gamma"]
                row.theta = g["theta"]
                row.vega = g["vega"]
                row.greeks_source = g["greeks_source"]
                if g["greeks_source"] == "unavailable":
                    unavail_count += 1
            except Exception as exc:
                # Per-row failure must not abort the entire chain enrichment loop.
                logger.warning(
                    "[GREEKS-ENRICH-FAIL] strike=%.1f iv=%.4f reason=%s",
                    row.strike, iv, exc,
                )
                row.greeks_source = "unavailable"
                unavail_count += 1
        else:
            row.greeks_source = "unavailable"
            unavail_count += 1

    total = len(rows)
    if total > 0 and unavail_count / total > 0.5:
        logger.warning(
            "Greeks data degraded: %d/%d strikes missing greeks. "
            "Strike selector will skip unavailable rows.",
            unavail_count, total,
        )


class OptionsChainCache:
    """
    Thin wrapper around yfinance.Ticker.option_chain() with a 60-second TTL.
    Returns the nearest expiry that has at least MIN_STRIKES liquid strikes.
    """

    MIN_OI = 100          # minimum open interest to consider a strike liquid
    MIN_STRIKES = 5       # minimum number of liquid strikes before expiry is usable
    CACHE_TTL = 60        # seconds

    def __init__(self, ticker: str = "TSLA"):
        self.ticker = ticker
        self._cache: dict = {}        # expiry_date -> (timestamp, list[OptionRow])
        self._expiry_list: list = []
        self._expiry_ts: float = 0.0

    # ── expiry list ───────────────────────────────────────────────────────────

    def _get_expiry_list(self) -> list:
        """Return expiration dates from Tradier (primary) or yfinance (fallback)."""
        now = time.time()
        if now - self._expiry_ts < self.CACHE_TTL and self._expiry_list:
            return self._expiry_list

        source_env = os.getenv("OPTIONS_CHAIN_SOURCE", "auto")

        # Tradier expiry discovery (preferred)
        if source_env in ("tradier", "auto"):
            try:
                from ingestion.tradier_chain import get_expirations
                dates = get_expirations(self.ticker)
                if dates:
                    self._expiry_list = dates
                    self._expiry_ts = now
                    logger.debug("Tradier expirations for %s: %d dates", self.ticker, len(dates))
                    return self._expiry_list
            except RuntimeError as exc:
                if "401" in str(exc) or "Unauthorized" in str(exc):
                    logger.error("Tradier expirations: auth failure — %s", exc)
                    if source_env == "tradier":
                        raise
                else:
                    logger.warning("Tradier expirations failed (%s); falling back to yfinance", exc)
            except Exception as exc:
                logger.warning("Tradier expirations failed (%s); falling back to yfinance", exc)

        # yfinance fallback
        try:
            t = yf.Ticker(self.ticker)
            self._expiry_list = list(t.options)
            self._expiry_ts = now
            logger.debug(f"Fetched {len(self._expiry_list)} expiry dates for {self.ticker} (yfinance)")
        except Exception as e:
            logger.warning(f"Failed to fetch expiry list: {e}")
        return self._expiry_list

    # ── Tradier chain (real-time, native greeks) ──────────────────────────────

    def _fetch_chain_tradier(self, expiry: str) -> list[OptionRow]:
        """
        Fetch option chain from Tradier with native greeks.

        Maps Tradier's response fields to OptionRow.  Rows with greeks from
        Tradier have greeks_source="tradier" — enrich_greeks() skips them.
        Rows where Tradier greeks are null (very illiquid contracts) get
        greeks_source="unavailable" — no BS fallback (per spec).

        Returns an empty list on any failure (fallback to yfinance/IBKR applies).
        """
        try:
            from ingestion.tradier_chain import get_chain as tradier_get_chain
        except ImportError:
            logger.warning("tradier_chain module not available")
            return []

        try:
            raw_opts = tradier_get_chain(self.ticker, expiry)
        except RuntimeError as exc:
            if "401" in str(exc) or "Unauthorized" in str(exc):
                logger.error("Tradier chain: auth failure — %s", exc)
                raise  # propagate auth failure — caller decides fallback
            logger.warning("Tradier chain fetch failed for %s %s: %s", self.ticker, expiry, exc)
            return []
        except Exception as exc:
            logger.warning("Tradier chain fetch failed for %s %s: %s", self.ticker, expiry, exc)
            return []

        if not raw_opts:
            return []

        rows: list[OptionRow] = []
        for opt in raw_opts:
            try:
                greeks = opt.get("greeks") or {}

                # Determine greeks availability
                delta = greeks.get("delta")
                gamma = greeks.get("gamma")
                theta = greeks.get("theta")
                vega  = greeks.get("vega")

                # mid_iv is Tradier's implied vol estimate (mid-market)
                mid_iv = greeks.get("mid_iv") or greeks.get("smv_vol") or 0.0
                try:
                    mid_iv = float(mid_iv) if mid_iv is not None else 0.0
                except (TypeError, ValueError):
                    mid_iv = 0.0

                # Greeks source: "tradier" if at least delta is present, else "unavailable"
                if delta is not None:
                    greeks_source = "tradier"
                    try:
                        delta = float(delta)
                        gamma = float(gamma) if gamma is not None else None
                        theta = float(theta) if theta is not None else None
                        vega  = float(vega)  if vega  is not None else None
                    except (TypeError, ValueError):
                        greeks_source = "unavailable"
                        delta = gamma = theta = vega = None
                else:
                    # Very illiquid contract — Tradier doesn't compute greeks
                    greeks_source = "unavailable"
                    delta = gamma = theta = vega = None

                opt_type_raw = (opt.get("option_type") or "").lower()
                opt_type = "CALL" if opt_type_raw == "call" else "PUT"

                row = OptionRow(
                    strike=float(opt["strike"]),
                    option_type=opt_type,
                    expiration_date=expiry,
                    bid=float(opt.get("bid") or 0) or 0.0,
                    ask=float(opt.get("ask") or 0) or 0.0,
                    last_price=float(opt.get("last") or 0) or 0.0,
                    volume=int(opt.get("volume") or 0) or 0,
                    open_interest=int(opt.get("open_interest") or 0) or 0,
                    implied_volatility=mid_iv,
                    delta=delta,
                    gamma=gamma,
                    theta=theta,
                    vega=vega,
                    greeks_source=greeks_source,
                )
                rows.append(row)
            except (KeyError, TypeError, ValueError) as exc:
                logger.debug("Tradier chain row parse error: %s — row: %s", exc, opt)
                continue

        logger.info(
            "Tradier chain: %d contracts loaded for %s %s (greeks_native=%d)",
            len(rows), self.ticker, expiry,
            sum(1 for r in rows if r.greeks_source == "tradier"),
        )
        return rows

    # ── IBKR chain (off-hours, 24h data from paper account) ──────────────────

    def _fetch_chain_ibkr(self, expiry: str) -> list[OptionRow]:
        """
        Fetch option chain via IBKR paper account.
        Preferred off-hours: OI + bid/ask available around the clock.
        Returns an empty list on any failure (yfinance fallback applies).
        """
        try:
            from ingestion.ibkr_feed import get_ibkr_feed
            from ib_insync import Option
        except ImportError:
            return []

        try:
            feed = get_ibkr_feed()
            if not feed.is_connected():
                return []
            ib = feed._ib  # access underlying IB instance

            expiry_ib = expiry.replace("-", "")

            # Get the valid strikes for this expiry from IBKR
            params_list = ib.reqSecDefOptParams(self.ticker, "", "STK", 0)
            ib.sleep(2.0)

            strikes: set = set()
            for p in params_list:
                if p.exchange == "SMART" and expiry_ib in p.expirations:
                    strikes.update(p.strikes)

            if not strikes:
                logger.debug("IBKR chain: no strikes found for %s %s", self.ticker, expiry)
                return []

            rows: list[OptionRow] = []
            # Limit to a reasonable number of strikes to avoid flooding IBKR.
            # Cap at 40 strikes centred around ATM to keep request volume manageable.
            sorted_strikes = sorted(strikes)
            if len(sorted_strikes) > 40:
                # Trim to 40 strikes nearest to the middle of the range
                mid_idx = len(sorted_strikes) // 2
                half = 20
                sorted_strikes = sorted_strikes[max(0, mid_idx - half): mid_idx + half]

            # ── Batch-qualify all contracts before reqMktData ─────────────────
            # IBKR Error 321 ("Invalid contract id") fires when reqMktData receives
            # an unqualified contract (conId == 0).  qualifyContracts fills conIds
            # via reqContractDetails in one round-trip per batch.
            #
            # FIX (Phase 14.6): Include currency="USD" and multiplier="100" so IBKR
            # doesn't return an ambiguous / generic contract.  Without these,
            # qualifyContracts may silently succeed but leave conId==0 because the
            # spec matched multiple listings.  tradingClass="TSLA" disambiguates
            # weekly vs monthly series.
            all_contracts = []
            contract_meta = []
            for right_chr, opt_type in [("C", "CALL"), ("P", "PUT")]:
                for strike_val in sorted_strikes:
                    c = Option(
                        self.ticker, expiry_ib, strike_val, right_chr, "SMART",
                        currency="USD", multiplier="100",
                        tradingClass=self.ticker,
                    )
                    all_contracts.append(c)
                    contract_meta.append((opt_type, strike_val))

            try:
                qualified = ib.qualifyContracts(*all_contracts)
                ib.sleep(1.0)  # allow qualification responses to arrive
            except Exception as qe:
                logger.warning("IBKR chain: qualifyContracts failed for %s %s: %s",
                               self.ticker, expiry, qe)
                return []

            # ── CHAIN-DIAG: log qualification results for each cycle ──────────
            n_qualified = sum(1 for q in (qualified or []) if q is not None and getattr(q, "conId", 0) > 0)
            logger.info(
                "[CHAIN-DIAG] expiry=%s strikes_requested=%d qualified_returned=%d",
                expiry, len(all_contracts), n_qualified,
            )
            for qc_sample in (qualified or [])[:5]:
                if qc_sample is not None:
                    logger.info(
                        "[CHAIN-DIAG] qualified sample: conId=%s right=%s strike=%s "
                        "exchange=%s currency=%s multiplier=%s tradingClass=%s",
                        getattr(qc_sample, "conId", "?"),
                        getattr(qc_sample, "right", "?"),
                        getattr(qc_sample, "strike", "?"),
                        getattr(qc_sample, "exchange", "?"),
                        getattr(qc_sample, "currency", "?"),
                        getattr(qc_sample, "multiplier", "?"),
                        getattr(qc_sample, "tradingClass", "?"),
                    )

            # ── Per-contract fallback if batch returned zero qualified ─────────
            # qualifyContracts can silently return an empty/None list for some IBKR
            # account types.  Fall back to individual reqContractDetails calls which
            # are slower (200ms each) but always work.
            if n_qualified == 0 and len(all_contracts) > 0:
                logger.info(
                    "[CHAIN-DIAG] batch qualification returned 0 — falling back to "
                    "per-contract reqContractDetails for %s %s",
                    self.ticker, expiry,
                )
                qualified_fallback = []
                for c in all_contracts:
                    try:
                        details = ib.reqContractDetails(c)
                        ib.sleep(0.2)
                        if details:
                            qualified_fallback.append(details[0].contract)
                        else:
                            qualified_fallback.append(None)
                    except Exception:
                        qualified_fallback.append(None)
                qualified = qualified_fallback

            # Build a map from (right, strike) → qualified contract for fast lookup.
            # Key by round(strike, 2) to avoid floating-point mismatch
            # (e.g. 250.0 sent vs 250.0000001 returned).
            qual_map: dict[tuple, object] = {}
            for qc in (qualified or []):
                if qc is not None and getattr(qc, "conId", 0) > 0:
                    key = (qc.right, round(float(qc.strike), 2))
                    qual_map[key] = qc

            # Diagnostic: log lookup failures before entering the data loop
            lookup_failures: list[tuple] = []
            for (opt_type, strike_val), _ in zip(contract_meta, all_contracts):
                right_chr = "C" if opt_type == "CALL" else "P"
                if (right_chr, round(float(strike_val), 2)) not in qual_map:
                    lookup_failures.append((right_chr, strike_val))
            logger.info(
                "[CHAIN-DIAG] qual_map_size=%d contracts_failing_lookup=%d sample=%s",
                len(qual_map), len(lookup_failures), lookup_failures[:5],
            )

            for (opt_type, strike_val), contract in zip(contract_meta, all_contracts):
                right_chr = "C" if opt_type == "CALL" else "P"
                key = (right_chr, round(float(strike_val), 2))
                qc = qual_map.get(key)
                if qc is None:
                    logger.debug(
                        "[CHAIN-SKIP] reason=qualification_failed strike=%s right=%s",
                        strike_val, right_chr,
                    )
                    continue
                # Hard guard: never call reqMktData on a zero-conId contract (causes Error 321)
                if getattr(qc, "conId", 0) == 0:
                    logger.warning(
                        "[CHAIN-SKIP] reason=conId_zero strike=%s right=%s — skipping to prevent Error 321",
                        strike_val, right_chr,
                    )
                    continue

                ticker_data = ib.reqMktData(qc, "", True, False)  # snapshot
                ib.sleep(0.15)

                bid  = float(ticker_data.bid  or 0)
                ask  = float(ticker_data.ask  or 0)
                last = float(ticker_data.last or ticker_data.close or 0)
                oi   = int(getattr(ticker_data, "openInterest", 0) or 0)
                if oi == 0:
                    # fall back to volume as OI proxy off-hours
                    oi = int(getattr(ticker_data, "volume", 0) or 0)
                iv   = float(getattr(ticker_data, "impliedVol", 0) or 0)
                vol_today = int(getattr(ticker_data, "volume", 0) or 0)

                row = OptionRow(
                    strike=strike_val,
                    option_type=opt_type,
                    expiration_date=expiry,
                    implied_volatility=iv,
                    open_interest=oi,
                    bid=bid,
                    ask=ask,
                    last_price=last,
                    volume=vol_today,
                )

                # IBKR modelGreeks — prefer native greeks when available
                model_greeks = getattr(ticker_data, "modelGreeks", None)
                if model_greeks is not None:
                    try:
                        row.delta = float(model_greeks.delta or 0)
                        row.gamma = float(model_greeks.gamma or 0)
                        row.theta = float(model_greeks.theta or 0)
                        row.vega  = float(model_greeks.vega or 0)
                        row.greeks_source = "ibkr"
                    except Exception:
                        pass  # fall through to BS-compute

                rows.append(row)

            logger.info(
                "IBKR chain: %d contracts loaded for %s %s (source=ibkr, qualified=%d/%d)",
                len(rows), self.ticker, expiry, len(qual_map), len(all_contracts),
            )
            return rows

        except Exception as exc:
            logger.debug("IBKR chain fetch failed (will fall back to yfinance): %s", exc)
            return []

    # ── chain for one expiry ──────────────────────────────────────────────────

    def _fetch_chain(self, expiry: str) -> list[OptionRow]:
        """Fetch and parse calls + puts for a given expiry date."""
        t = yf.Ticker(self.ticker)
        chain = t.option_chain(expiry)
        rows: list[OptionRow] = []

        for opt_type, df in [("CALL", chain.calls), ("PUT", chain.puts)]:
            for _, r in df.iterrows():
                try:
                    rows.append(OptionRow(
                        strike=float(r["strike"]),
                        option_type=opt_type,
                        expiration_date=expiry,
                        implied_volatility=float(r.get("impliedVolatility", 0.0)),
                        open_interest=int(r.get("openInterest", 0)),
                        bid=float(r.get("bid", 0.0)),
                        ask=float(r.get("ask", 0.0)),
                        last_price=float(r.get("lastPrice", 0.0)),
                        volume=int(r.get("volume", 0) or 0),
                    ))
                except Exception:
                    continue
        return rows

    def get_chain(self, expiry: str) -> list[OptionRow]:
        """
        Return cached (or fresh) option rows for `expiry`.

        Source priority controlled by OPTIONS_CHAIN_SOURCE env var:
          "tradier" → Tradier only, fail loud if unavailable
          "yfinance" → yfinance only
          "ibkr"    → IBKR only (requires OPRA subscription)
          "auto"    → Tradier → yfinance → IBKR cascade (default)
        """
        now = time.time()
        cached = self._cache.get(expiry)
        if cached and now - cached[0] < self.CACHE_TTL:
            return cached[1]

        source_env = os.getenv("OPTIONS_CHAIN_SOURCE", "auto")
        logger.info("get_chain: source_env=%s, expiry=%s, ticker=%s", source_env, expiry, self.ticker)
        rows: list[OptionRow] = []
        source = "unavailable"

        # ── 1. Tradier (preferred) ────────────────────────────────────────────
        if source_env in ("tradier", "auto"):
            try:
                rows = self._fetch_chain_tradier(expiry)
                if rows:
                    source = "tradier"
                    logger.info("get_chain: tradier returned %d rows for %s", len(rows), expiry)
            except RuntimeError as exc:
                if "401" in str(exc) or "Unauthorized" in str(exc):
                    logger.error("Tradier auth failure — cannot fetch chain: %s", exc)
                    if source_env == "tradier":
                        raise RuntimeError(
                            f"Tradier chain empty/failed for {expiry} and OPTIONS_CHAIN_SOURCE=tradier (no fallback)"
                        ) from exc
                # non-auth error: fall through to next source
            if not rows and source_env == "tradier":
                raise RuntimeError(
                    f"Tradier chain empty for {expiry} and OPTIONS_CHAIN_SOURCE=tradier (no fallback)"
                )

        # ── 2. yfinance fallback ──────────────────────────────────────────────
        if not rows and source_env in ("yfinance", "auto"):
            try:
                rows = self._fetch_chain(expiry)
                if rows:
                    source = "yfinance"
            except Exception as e:
                logger.warning("yfinance chain fetch failed for %s: %s", expiry, e)

        # ── 3. IBKR fallback (requires OPRA subscription) ────────────────────
        if not rows and source_env in ("ibkr", "auto"):
            try:
                rows = self._fetch_chain_ibkr(expiry)
                if rows:
                    source = "ibkr"
            except Exception as e:
                logger.warning("IBKR chain fetch failed for %s: %s", expiry, e)

        if not rows:
            logger.warning("All chain sources empty for %s", expiry)
            return cached[1] if cached else []

        # ── Enrich greeks — skip rows that already have native greeks ─────────
        # Tradier rows with greeks_source="tradier" already have delta/gamma/theta/vega.
        # enrich_greeks() internally skips rows where greeks_source == "ibkr";
        # we extend that skip to "tradier" rows here for efficiency.
        needs_enrich = any(r.greeks_source not in ("tradier", "ibkr") for r in rows)
        if needs_enrich:
            from datetime import date as _d
            try:
                exp_date = _d.fromisoformat(expiry)
                dte = (exp_date - _d.today()).days
                ttm = max(dte / 365.0, 0.0001)
            except ValueError:
                ttm = 7 / 365.0
            try:
                _spot, _ = get_spot_with_fallback(self.ticker)
            except Exception:
                _spot = 0.0
            # Temporarily mark tradier rows so enrich_greeks skips them
            tradier_rows = [r for r in rows if r.greeks_source == "tradier"]
            for r in tradier_rows:
                r.greeks_source = "ibkr"  # borrow the skip sentinel
            try:
                enrich_greeks(rows, spot=_spot, ttm_years=ttm)
            except Exception as _ge:
                logger.warning("enrich_greeks call failed for %s: %s", expiry, _ge)
            finally:
                # Restore tradier label
                for r in tradier_rows:
                    r.greeks_source = "tradier"

        self._cache[expiry] = (now, rows)
        logger.info("Options chain loaded: %s — %d contracts (source=%s)", expiry, len(rows), source)
        _hb("options_chain_api", status="ok", detail=f"expiry:{expiry} contracts:{len(rows)} source:{source}")
        if source == "tradier":
            _hb("tradier_chain", status="ok", detail=f"expiries=1 rows={len(rows)}")
        return rows

    # ── public API ────────────────────────────────────────────────────────────

    def nearest_expiry_with_liquidity(self, min_dte: int = 1) -> Optional[str]:
        """
        Return the nearest expiry date that has >= MIN_STRIKES liquid strikes
        and is at least min_dte days away.
        """
        from datetime import date, timedelta
        today = date.today()
        cutoff = today + timedelta(days=min_dte)

        for expiry in self._get_expiry_list():
            try:
                exp_date = date.fromisoformat(expiry)
            except ValueError:
                continue
            if exp_date < cutoff:
                continue
            rows = self.get_chain(expiry)
            liquid = [r for r in rows if r.open_interest >= self.MIN_OI]
            if len(liquid) >= self.MIN_STRIKES:
                return expiry
        return None

    def snap_strike(
        self,
        spot: float,
        option_type: str,
        target_moneyness: float = 1.05,
        expiry: Optional[str] = None,
    ) -> tuple[float, float, str]:
        """
        Find the nearest liquid strike to `spot * target_moneyness`.

        Returns: (snapped_strike, implied_volatility, expiry_date)
        Falls back to the simple formula if no chain data is available.
        """
        if expiry is None:
            expiry = self.nearest_expiry_with_liquidity(min_dte=1)

        if not expiry:
            fallback = round_to_chain_increment(spot * target_moneyness)
            logger.warning("No liquid expiry found — using formula strike")
            return fallback, 0.0, ""

        rows = self.get_chain(expiry)
        candidates = [
            r for r in rows
            if r.option_type == option_type and r.open_interest >= self.MIN_OI
        ]
        if not candidates:
            fallback = round_to_chain_increment(spot * target_moneyness)
            logger.warning(f"No liquid {option_type} strikes for {expiry} — using formula")
            return fallback, 0.0, expiry

        target_strike = spot * target_moneyness
        best = min(candidates, key=lambda r: abs(r.strike - target_strike))
        logger.info(
            f"Snapped {option_type} strike: {best.strike} "
            f"(target={target_strike:.1f}, IV={best.implied_volatility:.2%}, "
            f"OI={best.open_interest})"
        )
        return best.strike, best.implied_volatility, expiry

    def get_iv_for_strike(
        self,
        strike: float,
        option_type: str,
        expiry: str,
    ) -> float:
        """Look up IV for an exact strike/type/expiry. Returns 0.0 if not found."""
        rows = self.get_chain(expiry)
        for r in rows:
            if r.option_type == option_type and abs(r.strike - strike) < 0.01:
                return r.implied_volatility
        return 0.0

    # ── delta-based strike selection ─────────────────────────────────────────

    @staticmethod
    def _bs_call_delta(S: float, K: float, T: float, r: float, sigma: float) -> float:
        """Black-Scholes call delta = N(d1). Returns value in (0, 1)."""
        import math
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
            return 0.5  # ATM fallback
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        return 0.5 * math.erfc(-d1 / math.sqrt(2))

    def snap_strike_by_delta(
        self,
        spot: float,
        option_type: str,
        target_delta: float,
        expiry: Optional[str] = None,
        max_delta_error: float = 0.05,
    ) -> tuple[float, float, str]:
        """
        Find the chain strike whose Black-Scholes delta is closest to target_delta.

        For calls, delta in (0, 1).  For puts, pass the absolute value (e.g. 0.25
        means 25-delta put); this method handles the sign internally.

        Returns (strike, implied_volatility, expiry_date).
        Returns (-1.0, 0.0, expiry) if no strike is within max_delta_error — caller
        must reject the signal.
        Falls back to snap_strike (moneyness-based) if the chain is entirely missing.
        """
        from datetime import date as _date_cls

        if expiry is None:
            expiry = self.nearest_expiry_with_liquidity(min_dte=1)

        if not expiry:
            moneyness = 1.0 + (target_delta - 0.5) * 0.2
            return self.snap_strike(spot, option_type, moneyness)

        rows = self.get_chain(expiry)
        candidates = [
            r for r in rows
            if r.option_type == option_type and r.open_interest >= self.MIN_OI
        ]
        if not candidates:
            moneyness = 1.0 + (target_delta - 0.5) * 0.2
            return self.snap_strike(spot, option_type, moneyness, expiry=expiry)

        try:
            exp_date = _date_cls.fromisoformat(expiry)
            dte = (_date_cls.today() - exp_date).days  # negative = future
            dte = (exp_date - _date_cls.today()).days
        except ValueError:
            dte = 7
        T = max(dte / 365.0, 0.001)
        r_rate = 0.05  # risk-free rate approximation

        best: Optional[OptionRow] = None
        best_err = float("inf")

        for row in candidates:
            iv = row.implied_volatility
            if iv <= 0:
                continue
            call_delta = self._bs_call_delta(spot, row.strike, T, r_rate, iv)
            delta = call_delta if option_type == "CALL" else (1.0 - call_delta)
            err = abs(delta - target_delta)
            if err < best_err:
                best_err = err
                best = row

        if best is None or best_err > max_delta_error:
            if best is None:
                logger.warning(
                    "snap_strike_by_delta: no IV available for %s %s — using moneyness fallback",
                    option_type, expiry,
                )
                moneyness = 1.0 + (target_delta - 0.5) * 0.2
                return self.snap_strike(spot, option_type, moneyness, expiry=expiry)
            else:
                logger.warning(
                    "snap_strike_by_delta: closest delta err=%.3f exceeds max_delta_error=%.2f "
                    "for %s %s target_delta=%.2f — signal rejected",
                    best_err, max_delta_error, option_type, expiry, target_delta,
                )
                return -1.0, 0.0, expiry  # sentinel: caller must reject signal

        logger.info(
            "snap_strike_by_delta: %s strike=%.1f target_delta=%.2f err=%.3f IV=%.1f%%",
            option_type, best.strike, target_delta, best_err, best.implied_volatility * 100,
        )
        return best.strike, best.implied_volatility, expiry


# ── multi-source spot price with fallback chain ───────────────────────────────

def get_spot_with_fallback(symbol: str = "TSLA") -> tuple[float, str]:
    """
    Fetch spot price with 3-tier fallback: IBKR → TradingView → yfinance.
    Returns (price, source_name).
    """
    # 1. Try IBKR (primary — real-time paper account data)
    try:
        from ingestion.ibkr_feed import get_ibkr_feed, IBKRNotConnectedError
        feed = get_ibkr_feed()
        if feed.is_connected():
            spot = feed.get_spot(symbol)
            price = spot["price"]
            if price and price > 0:
                logger.info(f"Spot from IBKR: ${price:.2f}")
                return price, "ibkr"
    except Exception as e:
        logger.debug(f"IBKR spot skipped: {e}")

    # 2. Try TradingView
    try:
        from ingestion.tv_feed import get_tv_cache
        tv_price = get_tv_cache().get_spot(symbol)
        if tv_price and tv_price > 0:
            logger.info(f"Spot from TradingView: ${tv_price:.2f}")
            return tv_price, "tv"
    except Exception as e:
        logger.debug(f"TradingView spot skipped: {e}")

    # 3. Fallback to yfinance
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1d")
        if not hist.empty:
            price = float(hist["Close"].iloc[-1])
            logger.info(f"Spot from yfinance: ${price:.2f}")
            return price, "yfinance"
    except Exception as e:
        logger.warning(f"yfinance spot failed: {e}")

    logger.error(f"All spot sources failed for {symbol}")
    return 0.0, "unavailable"


# Module-level singleton — shared across all publisher calls
_chain_cache: Optional[OptionsChainCache] = None


def get_chain_cache() -> OptionsChainCache:
    global _chain_cache
    if _chain_cache is None:
        _chain_cache = OptionsChainCache("TSLA")
    return _chain_cache


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cache = OptionsChainCache("TSLA")
    expiry = cache.nearest_expiry_with_liquidity(min_dte=1)
    print(f"Nearest liquid expiry: {expiry}")
    if expiry:
        strike, iv, exp = cache.snap_strike(380.0, "CALL", target_moneyness=1.05, expiry=expiry)
        print(f"Snapped CALL strike: ${strike:.1f}  IV: {iv:.1%}  Expiry: {exp}")
        strike, iv, exp = cache.snap_strike(380.0, "PUT", target_moneyness=0.95, expiry=expiry)
        print(f"Snapped PUT strike:  ${strike:.1f}  IV: {iv:.1%}  Expiry: {exp}")

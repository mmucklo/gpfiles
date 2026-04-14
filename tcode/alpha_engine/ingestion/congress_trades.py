#!/usr/bin/env python3
"""
Congressional STOCK Act Disclosure Lag-Arb Intelligence

Why this signal exists:
  STOCK Act (2012) mandates Congress members disclose stock trades within 45 days.
  Academic studies (Eggers & Hainmueller 2014; Karadas 2019) show senators on key
  committees earn 5-12% abnormal annual returns vs market. Disclosures create a
  predictable 48-hour reaction window as retail/algorithmic traders pile in after
  detecting institutional-grade information flow.

Data sources (free, public):
  - Senate: Senate Electronic Financial Disclosures (eFTS) JSON search API
    https://efts.senate.gov/LATEST/search-index
  - House: House Disclosure Clerk annual ZIP (Phase 13.5 fix)
    https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip
    (The prior URL pattern ptr-pdfs/{year}FD.xml returned 404 as of 2026.)
    NOTE: The ZIP contains a filing INDEX only — no per-transaction ticker data.
    House PTR is currently DISABLED pending discovery of a transaction-level source.
    TODO(phase-13.5): Investigate individual DocID PDFs or alternate data source
    (QuiverQuant requires API key; OpenSecrets per-ticker endpoint needs auth).
    Annual URL discovery: verify each January — pattern is financial-pdfs/{year}FD.zip

Committee weighting rationale:
  Senate Commerce, Science & Transportation: oversees FCC, FTC, NHTSA, and EV/tech.
  House Energy & Commerce: primary jurisdiction over energy, EVs, autonomous vehicles,
  and consumer electronics. Members routinely receive NHTSA/DOE briefings before public.
  All other committees: baseline 1.0× weight.

Signal emission criteria (both must be true):
  1. Filing date < 48 hours ago (early-reaction window)
  2. Trade amount ≥ $15,001 (SEC materiality threshold for disclosure)
"""
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from xml.etree import ElementTree as ET

import requests

logger = logging.getLogger("CongressTrades")

_CACHE: Optional[dict] = None
_CACHE_TS: float = 0.0
_CACHE_TTL = 3600  # 1 hour — disclosures trickle in; no need to hammer gov servers

_REQUEST_TIMEOUT = 10  # seconds

# ── Senate eFTS circuit breaker state ────────────────────────────────────────
# After 3 consecutive failures, mark degraded for 10 minutes to avoid hammering DNS.
_SENATE_FAIL_COUNT: int = 0
_SENATE_DEGRADED_UNTIL: float = 0.0
_SENATE_LAST_SUCCESS_AT: Optional[str] = None
_SENATE_LAST_ERROR: Optional[str] = None
_SENATE_DEGRADED_RETRY_INTERVAL: float = 600  # 10 minutes
_SENATE_MAX_RETRIES: int = 3
_SENATE_RETRY_DELAYS: tuple = (1, 4, 16)  # exponential backoff seconds

# ── House PTR URL pattern ─────────────────────────────────────────────────────
# The old ptr-pdfs/{year}FD.xml path returned 404 as of 2026. The new ZIP is
# available at financial-pdfs/{year}FD.zip but contains only a filing INDEX
# (member names + DocIDs), NOT per-transaction ticker data.
# Pattern: https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip
# House PTR is DISABLED until a transaction-level source is identified.
# Source: verified 2026-04-14 that ZIP returns 200 but has no <Asset>/<Transaction> nodes.
HOUSE_PTR_URL_PATTERN = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
_HOUSE_DISABLED_REASON = (
    "House PTR feed disabled — annual URL discovery required. "
    "The {year}FD.zip index contains filing metadata only (no per-ticker transaction data). "
    "See TODO(phase-13.5) in congress_trades.py."
)

# ── Committee membership: 119th Congress (2025–2026) ──────────────────────────
# Source: congress.gov committee listings, updated manually each session.
# High-relevance = Senate Commerce or House Energy & Commerce.
# These committees have direct oversight of EVs, autonomous vehicles, NHTSA, DOE.
_HIGH_RELEVANCE_COMMITTEES = {
    # Senate Committee on Commerce, Science, and Transportation
    "Senate Commerce", "Commerce, Science, and Transportation",
    "Committee on Commerce, Science, and Transportation",
    # House Committee on Energy and Commerce
    "House Energy and Commerce", "Energy and Commerce",
    "Committee on Energy and Commerce",
    "Energy & Commerce",
}

# Key members of high-relevance committees — name lookups for when committee
# field isn't populated in the filing metadata. Last name → committee.
_COMMITTEE_MEMBERS_119TH: dict[str, str] = {
    # Senate Commerce (partial — chairs and ranking members)
    "Wicker": "Senate Commerce",
    "Cantwell": "Senate Commerce",
    "Cruz": "Senate Commerce",
    "Klobuchar": "Senate Commerce",
    "Capito": "Senate Commerce",
    "Blackburn": "Senate Commerce",
    "Peters": "Senate Commerce",
    "Blumenthal": "Senate Commerce",
    "Moran": "Senate Commerce",
    "Rosen": "Senate Commerce",
    "Young": "Senate Commerce",
    "Lujan": "Senate Commerce",
    "Thune": "Senate Commerce",
    "Fischer": "Senate Commerce",
    "Sullivan": "Senate Commerce",
    "Daines": "Senate Commerce",
    "Schmitt": "Senate Commerce",
    "Budd": "Senate Commerce",
    # House Energy & Commerce (partial)
    "Guthrie": "House Energy and Commerce",
    "Pallone": "House Energy and Commerce",
    "Rogers": "House Energy and Commerce",
    "Castor": "House Energy and Commerce",
    "Latta": "House Energy and Commerce",
    "DeGette": "House Energy and Commerce",
    "Burgess": "House Energy and Commerce",
    "Schakowsky": "House Energy and Commerce",
    "Walden": "House Energy and Commerce",
    "Tonko": "House Energy and Commerce",
    "Carter": "House Energy and Commerce",
    "Matsui": "House Energy and Commerce",
    "Duncan": "House Energy and Commerce",
    "Bilirakis": "House Energy and Commerce",
}


def _committee_weight(last_name: str, committee_field: str = "") -> float:
    """
    Return 2.0× for members of Senate Commerce or House Energy & Commerce,
    1.0× for all others.

    Checks the committee_field string first (explicit from filing),
    then falls back to the static member roster.
    """
    if committee_field:
        for hrc in _HIGH_RELEVANCE_COMMITTEES:
            if hrc.lower() in committee_field.lower():
                return 2.0
    if last_name in _COMMITTEE_MEMBERS_119TH:
        return 2.0
    return 1.0


def _parse_amount_lower_bound(amount_str: str) -> int:
    """
    Parse a range string like '$15,001 - $50,000' → 15001 (lower bound).
    Returns 0 if unparseable.
    """
    try:
        # Strip dollar signs, commas, spaces; take first number before ' - '
        raw = amount_str.split("-")[0]
        raw = raw.replace("$", "").replace(",", "").replace(" ", "")
        return int(float(raw))
    except (ValueError, IndexError, AttributeError):
        return 0


def _is_within_48h(date_str: str) -> bool:
    """
    Return True if date_str (YYYY-MM-DD or MM/DD/YYYY) is within the last 48 hours.
    Uses UTC for comparison to avoid DST edge cases.

    For date-only strings (no time component), comparison is against calendar date
    of the 48h cutoff — a filing on the same calendar date as the cutoff is treated
    as within 48h because congressional disclosures carry no intra-day timestamp.
    """
    if not date_str:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            if "T" in fmt:
                # Full ISO datetime — compare with full cutoff
                dt = datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
                return dt >= cutoff
            else:
                # Date-only — compare calendar dates (filing on cutoff's date = within 48h)
                dt = datetime.strptime(date_str[:10], fmt).replace(tzinfo=timezone.utc)
                return dt.date() >= cutoff.date()
        except ValueError:
            continue
    return False


def _fetch_senate_ptrs() -> list[dict]:
    """
    Query Senate Electronic Financial Disclosures (eFTS) for recent TSLA PTR filings.

    The eFTS search API returns JSON with filing metadata. Transaction-level detail
    (amount, direction) requires parsing the individual PTR document; for signal
    generation we use the metadata + senator/committee info.

    Reference: https://efts.senate.gov — no auth required, rate limit unclear.

    Resilience (Phase 13.5):
      - 3 retry attempts with exponential backoff (1s, 4s, 16s)
      - After 3 consecutive failures, source is marked degraded for 10 minutes
      - Logs [CONGRESS-DEGRADED] on circuit open; [CONGRESS-RECOVERED] on success
    """
    global _SENATE_FAIL_COUNT, _SENATE_DEGRADED_UNTIL, _SENATE_LAST_SUCCESS_AT, _SENATE_LAST_ERROR

    now_ts = time.time()

    # Circuit breaker: if degraded, don't attempt until cooldown expires
    if _SENATE_DEGRADED_UNTIL > 0 and now_ts < _SENATE_DEGRADED_UNTIL:
        next_retry = datetime.fromtimestamp(_SENATE_DEGRADED_UNTIL, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        logger.debug("[CONGRESS-DEGRADED] Senate eFTS circuit open — next_retry_at=%s", next_retry)
        return []

    now_dt = datetime.now(timezone.utc)
    from_date = (now_dt - timedelta(days=3)).strftime("%Y-%m-%d")
    to_date = now_dt.strftime("%Y-%m-%d")

    url = "https://efts.senate.gov/LATEST/search-index"
    params = {
        "q": '"TSLA"',
        "dateRange": "custom",
        "fromDate": from_date,
        "toDate": to_date,
        "category": "Periodic Transactions Report",
        "results": "25",
    }

    last_exc: Optional[Exception] = None
    for attempt in range(_SENATE_MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            # Success — reset circuit breaker
            if _SENATE_FAIL_COUNT > 0:
                logger.info("[CONGRESS-RECOVERED] Senate eFTS recovered after %d failures", _SENATE_FAIL_COUNT)
            _SENATE_FAIL_COUNT = 0
            _SENATE_DEGRADED_UNTIL = 0.0
            _SENATE_LAST_SUCCESS_AT = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            _SENATE_LAST_ERROR = None
            break
        except (requests.RequestException, json.JSONDecodeError, ValueError) as e:
            last_exc = e
            if attempt < _SENATE_MAX_RETRIES - 1:
                delay = _SENATE_RETRY_DELAYS[attempt]
                logger.debug("Senate eFTS attempt %d/%d failed (%s), retrying in %ds",
                             attempt + 1, _SENATE_MAX_RETRIES, e, delay)
                time.sleep(delay)
    else:
        # All _SENATE_MAX_RETRIES attempts exhausted — open circuit immediately.
        # The spec says "after 3 failures, mark degraded": those 3 failures are
        # the 3 retry attempts within a single call (not 3 separate calls).
        _SENATE_FAIL_COUNT += 1
        _SENATE_LAST_ERROR = str(last_exc)
        _SENATE_DEGRADED_UNTIL = time.time() + _SENATE_DEGRADED_RETRY_INTERVAL
        next_retry = datetime.fromtimestamp(_SENATE_DEGRADED_UNTIL, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        logger.warning(
            "[CONGRESS-DEGRADED] reason=%s next_retry_at=%s",
            _SENATE_LAST_ERROR, next_retry,
        )
        return []

    trades = []
    hits = data.get("hits", {}).get("hits", [])
    for hit in hits:
        src = hit.get("_source", {})
        first = src.get("first_name", "")
        last = src.get("last_name", "")
        date_filed = src.get("date_filed", "") or src.get("date", "")
        link = src.get("link", "")

        # eFTS doesn't expose transaction level in search; mark as UNKNOWN direction.
        # We still emit the filing event for committee-weighted confidence adjustment.
        committee = src.get("committee", "") or ""
        weight = _committee_weight(last, committee)

        # Senate minimum disclosure threshold is $1,001; material threshold is $15,001
        # Without parsing the PDF we assume compliance filing → use filing as signal
        trades.append({
            "source": "SENATE",
            "name": f"{first} {last}".strip(),
            "last_name": last,
            "date_filed": date_filed,
            "transaction_type": src.get("transaction_type", "UNKNOWN"),
            "amount": src.get("amount", ""),
            "amount_lower": _parse_amount_lower_bound(src.get("amount", "")),
            "ticker": "TSLA",
            "committee": committee,
            "committee_weight": weight,
            "link": link,
            "within_48h": _is_within_48h(date_filed),
        })

    return trades


def _fetch_house_ptrs() -> list[dict]:
    """
    Fetch House Periodic Transaction Reports.

    STATUS: DISABLED as of Phase 13.5 (2026-04-14).

    Investigation found:
      - https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}FD.xml → 404
      - https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip → 200
        BUT the ZIP contains only a filing INDEX (member names + DocIDs), not
        per-transaction ticker/amount data. The <Asset>, <TransactionType>, and
        <Amount> fields expected by _parse_house_xml() are absent.
      - QuiverQuant API requires authentication (no free unauthenticated endpoint).
      - JSON API at /api/v1/public_disc/ptr → 404.

    TODO(phase-13.5): Identify a transaction-level source for House PTRs.
    Options to investigate next:
      a) Fetch individual DocID PDFs (requires PDF parsing library — pyPDF2 or pdfplumber)
      b) Obtain QuiverQuant or OpenSecrets API key
      c) Check if eFTS/House adds per-transaction XML by next annual update
    Annual URL: verify HOUSE_PTR_URL_PATTERN pattern each January.

    Returns empty list with a logged warning so the Congress card shows
    "House feed: annual URL discovery required" rather than silently missing data.
    """
    year = datetime.now(timezone.utc).year
    logger.warning(
        "[HOUSE-PTR-DISABLED] House PTR transaction feed unavailable (Phase 13.5). "
        "ZIP index at %s exists but lacks per-ticker data. See TODO(phase-13.5).",
        HOUSE_PTR_URL_PATTERN.format(year=year),
    )
    return []


def _parse_house_xml(xml_text: str) -> list[dict]:
    """
    Parse House PTR XML and extract TSLA transactions.

    Handles the House eFD XML schema. Returns a list of trade dicts
    compatible with the unified trades format.
    """
    trades = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning(f"House XML parse error: {e}")
        return []

    # Support both single-disclosure and multi-disclosure XML roots
    disclosures = root.findall(".//FinancialDisclosure")
    if not disclosures:
        disclosures = [root]  # root IS the disclosure

    for disclosure in disclosures:
        member_el = disclosure.find("Member")
        if member_el is None:
            continue

        first = (member_el.findtext("First") or "").strip()
        last = (member_el.findtext("Last") or "").strip()
        state = (member_el.findtext("State") or "").strip()

        transactions_el = disclosure.find("Transactions")
        if transactions_el is None:
            continue

        for txn in transactions_el.findall("Transaction"):
            asset = (txn.findtext("Asset") or "").upper()
            # Match TSLA by ticker symbol in brackets or asset name
            if "TSLA" not in asset and "TESLA" not in asset:
                continue

            filing_date  = (txn.findtext("FilingDate") or "").strip()
            txn_date     = (txn.findtext("TransactionDate") or filing_date).strip()
            txn_type_raw = (txn.findtext("TransactionType") or "").strip().upper()
            amount_str   = (txn.findtext("Amount") or "").strip()
            committee    = (txn.findtext("Committee") or "").strip()

            # Normalize transaction type
            if txn_type_raw in ("P", "PURCHASE", "BUY"):
                txn_type = "PURCHASE"
            elif txn_type_raw in ("S", "SALE", "SELL", "S (PARTIAL)"):
                txn_type = "SALE"
            else:
                txn_type = txn_type_raw or "UNKNOWN"

            amount_lower = _parse_amount_lower_bound(amount_str)
            # $15,001 materiality threshold — skip filings below this
            if amount_lower > 0 and amount_lower < 15001:
                continue

            weight = _committee_weight(last, committee)

            trades.append({
                "source": "HOUSE",
                "name": f"{first} {last} ({state})".strip(" ()"),
                "last_name": last,
                "date_filed": filing_date,
                "transaction_type": txn_type,
                "amount": amount_str,
                "amount_lower": amount_lower,
                "ticker": "TSLA",
                "committee": committee,
                "committee_weight": weight,
                "link": "",
                "within_48h": _is_within_48h(txn_date or filing_date),
            })

    return trades


def get_senate_status() -> dict:
    """
    Return current Senate eFTS source status for /api/intel congress sub-object.

    Status values:
      "ok"       — last fetch succeeded
      "degraded" — circuit open (3+ failures), retrying at next_retry_at
      "unknown"  — never successfully fetched in this process lifetime
    """
    now_ts = time.time()
    if _SENATE_DEGRADED_UNTIL > 0 and now_ts < _SENATE_DEGRADED_UNTIL:
        next_retry = datetime.fromtimestamp(_SENATE_DEGRADED_UNTIL, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "status": "degraded",
            "last_success_at": _SENATE_LAST_SUCCESS_AT,
            "last_error": _SENATE_LAST_ERROR,
            "next_retry_at": next_retry,
        }
    if _SENATE_LAST_SUCCESS_AT:
        return {"status": "ok", "last_success_at": _SENATE_LAST_SUCCESS_AT, "last_error": None}
    return {"status": "unknown", "last_success_at": None, "last_error": _SENATE_LAST_ERROR}


def get_congress_trades() -> dict:
    """
    Return congress trade intelligence. Cached 1 hour.

    Result schema:
      trades:       list of raw trade dicts (all TSLA filings found)
      recent_trades: trades within 48h with amount >= $15,001
      signal:       "BULLISH" | "BEARISH" | "NEUTRAL"
      committee_weighted_buy_48h:  bool — committee member buying in 48h
      committee_weighted_sell_48h: bool — committee member selling in 48h
      sentiment_multiplier: float — ×1.15 for buying, ×0.85 for selling, 1.0 neutral
      filing_count: int
      last_fetch_ts: float
      senate: {status, last_success_at, last_error}  — source health (Phase 13.5)
      house:  {status, last_success_at, last_error}  — "disabled" until PTR source found
    """
    global _CACHE, _CACHE_TS
    now = time.time()
    if _CACHE is not None and now - _CACHE_TS < _CACHE_TTL:
        return _CACHE

    senate_trades = _fetch_senate_ptrs()
    house_trades  = _fetch_house_ptrs()
    all_trades    = senate_trades + house_trades

    # Filter to recent (48h) trades meeting materiality threshold
    recent = [
        t for t in all_trades
        if t.get("within_48h") and t.get("amount_lower", 0) >= 15001
    ]

    # Check for committee-weighted activity in 48h window
    committee_buy  = any(
        t["committee_weight"] >= 2.0 and t["transaction_type"] == "PURCHASE"
        for t in recent
    )
    committee_sell = any(
        t["committee_weight"] >= 2.0 and t["transaction_type"] == "SALE"
        for t in recent
    )

    # Senate-only filings may have UNKNOWN type — treat as neutral for sell detection
    # but note the filing exists for alerting
    recent_buys  = [t for t in recent if t["transaction_type"] == "PURCHASE"]
    recent_sells = [t for t in recent if t["transaction_type"] == "SALE"]

    if committee_buy and not committee_sell:
        signal = "BULLISH"
        sentiment_multiplier = 1.15
    elif committee_sell and not committee_buy:
        signal = "BEARISH"
        sentiment_multiplier = 0.85
    elif recent_buys and not recent_sells:
        signal = "BULLISH"
        sentiment_multiplier = 1.10
    elif recent_sells and not recent_buys:
        signal = "BEARISH"
        sentiment_multiplier = 0.90
    else:
        signal = "NEUTRAL"
        sentiment_multiplier = 1.0

    result = {
        "trades": all_trades,
        "recent_trades": recent,
        "signal": signal,
        "committee_weighted_buy_48h": committee_buy,
        "committee_weighted_sell_48h": committee_sell,
        "sentiment_multiplier": sentiment_multiplier,
        "filing_count": len(all_trades),
        "recent_count": len(recent),
        "last_fetch_ts": now,
        # Source health sub-objects (Phase 13.5) — used by /api/intel and Congress card
        "senate": get_senate_status(),
        "house": {
            "status": "disabled",
            "last_success_at": None,
            "last_error": _HOUSE_DISABLED_REASON.format(year=datetime.now(timezone.utc).year),
        },
    }
    _CACHE = result
    _CACHE_TS = now
    return result


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    result = get_congress_trades()
    print(json.dumps(
        {k: v for k, v in result.items() if k != "trades"},  # omit full trade list
        indent=2,
        default=str,
    ))
    print(f"\nAll trades: {len(result['trades'])}, Recent (48h, $15k+): {len(result['recent_trades'])}")

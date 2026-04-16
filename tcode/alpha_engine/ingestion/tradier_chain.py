"""Tradier options chain client — real-time quotes with native greeks.

Endpoints used:
  GET /markets/options/expirations?symbol=TSLA
  GET /markets/options/chains?symbol=TSLA&expiration=YYYY-MM-DD&greeks=true
  GET /markets/quotes?symbols=TSLA

Auth: Authorization: Bearer {TRADIER_API_TOKEN}

Rate limit: 120 req/min (brokerage). We issue ~10-17 req/min — well within limit.

Error handling:
  401 → invalid/missing token; raises RuntimeError immediately (no fallback)
  429 → rate limited; retries up to 3× with 1s backoff
  5xx → transient; retries up to 3× with exponential backoff
  Empty/missing keys → log + return empty list (contract may have 0 listings)
"""

import os
import time
import logging

import requests

logger = logging.getLogger("TradierChain")

TRADIER_BASE_URL = os.getenv("TRADIER_BASE_URL", "https://api.tradier.com/v1")
TRADIER_API_TOKEN = os.getenv("TRADIER_API_TOKEN", "")

_RETRY_ATTEMPTS = 3
_RETRY_DELAY_S  = 1.0


def _get_headers() -> dict:
    token = os.getenv("TRADIER_API_TOKEN", TRADIER_API_TOKEN)
    if not token:
        raise RuntimeError(
            "TRADIER_API_TOKEN is not set. "
            "Get your token at https://developer.tradier.com/ and set it in .tsla-alpha.env"
        )
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }


def _request(method: str, path: str, params: dict | None = None) -> dict:
    """Make a retried HTTP request to the Tradier API. Returns parsed JSON."""
    base = os.getenv("TRADIER_BASE_URL", TRADIER_BASE_URL).rstrip("/")
    url  = f"{base}{path}"
    headers = _get_headers()

    last_exc: Exception | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            resp = requests.get(url, headers=headers, params=params or {}, timeout=10)

            if resp.status_code == 401:
                raise RuntimeError(
                    f"Tradier API 401 Unauthorized — check TRADIER_API_TOKEN. "
                    f"URL: {url}"
                )
            if resp.status_code == 429:
                wait = _RETRY_DELAY_S * (attempt + 1)
                logger.warning("Tradier rate-limited (429); retry %d/%d in %.1fs", attempt + 1, _RETRY_ATTEMPTS, wait)
                time.sleep(wait)
                last_exc = RuntimeError(f"Tradier 429 rate limit after {_RETRY_ATTEMPTS} retries")
                continue
            if resp.status_code >= 500:
                wait = _RETRY_DELAY_S * (2 ** attempt)
                logger.warning("Tradier 5xx (%d); retry %d/%d in %.1fs", resp.status_code, attempt + 1, _RETRY_ATTEMPTS, wait)
                time.sleep(wait)
                last_exc = RuntimeError(f"Tradier {resp.status_code} after {_RETRY_ATTEMPTS} retries")
                continue

            resp.raise_for_status()
            return resp.json()

        except (requests.RequestException, RuntimeError) as exc:
            if "401" in str(exc) or "Unauthorized" in str(exc):
                raise  # do not retry auth failures
            last_exc = exc
            if attempt < _RETRY_ATTEMPTS - 1:
                time.sleep(_RETRY_DELAY_S)

    raise RuntimeError(f"Tradier request failed after {_RETRY_ATTEMPTS} attempts: {last_exc}") from last_exc


def get_expirations(symbol: str = "TSLA") -> list[str]:
    """
    GET /markets/options/expirations?symbol=TSLA

    Returns a list of expiry date strings in YYYY-MM-DD format, e.g.:
      ["2026-04-17", "2026-04-24", "2026-05-02", ...]

    Returns empty list if the API returns no expirations for this symbol.
    """
    try:
        data = _request("GET", "/markets/options/expirations", {"symbol": symbol})
    except Exception as exc:
        logger.error("get_expirations(%s) failed: %s", symbol, exc)
        raise

    expirations = data.get("expirations") or {}
    dates = expirations.get("date") or []

    # API sometimes returns a single string instead of a list when only 1 expiry
    if isinstance(dates, str):
        dates = [dates]

    logger.debug("Tradier expirations for %s: %d dates", symbol, len(dates))
    return list(dates)


def get_chain(symbol: str, expiration: str) -> list[dict]:
    """
    GET /markets/options/chains?symbol=TSLA&expiration=2026-04-17&greeks=true

    Returns a list of option row dicts, each containing at minimum:
      strike, option_type, bid, ask, last, volume, open_interest, greeks (dict)

    greeks dict keys: delta, gamma, theta, vega, rho, phi, mid_iv, smv_vol

    Returns empty list when:
      - the expiry has no listed contracts
      - Tradier returns a null/empty options payload

    Raises RuntimeError on auth failure (401).
    """
    try:
        data = _request(
            "GET",
            "/markets/options/chains",
            {"symbol": symbol, "expiration": expiration, "greeks": "true"},
        )
    except Exception as exc:
        logger.error("get_chain(%s, %s) failed: %s", symbol, expiration, exc)
        raise

    options_wrapper = data.get("options") or {}
    option_list = options_wrapper.get("option") or []

    # API returns a dict (not a list) when there is exactly 1 contract
    if isinstance(option_list, dict):
        option_list = [option_list]

    if not option_list:
        logger.warning(
            "Tradier chain empty for %s %s (may have 0 listed contracts)", symbol, expiration
        )
        return []

    logger.debug("Tradier chain for %s %s: %d contracts", symbol, expiration, len(option_list))
    return list(option_list)


def get_quotes(symbol: str) -> dict:
    """
    GET /markets/quotes?symbols=TSLA

    Returns a quote dict containing (at minimum):
      last, bid, ask, change_percentage, volume, description

    Returns an empty dict on failure (non-auth failures are logged, not raised,
    since quotes are used only for spot-price cross-validation).
    """
    try:
        data = _request("GET", "/markets/quotes", {"symbols": symbol})
    except RuntimeError as exc:
        if "401" in str(exc):
            raise
        logger.warning("get_quotes(%s) failed: %s", symbol, exc)
        return {}
    except Exception as exc:
        logger.warning("get_quotes(%s) failed: %s", symbol, exc)
        return {}

    quotes_wrapper = data.get("quotes") or {}
    quote = quotes_wrapper.get("quote") or {}

    # When requesting multiple symbols, quote is a list; we only use one symbol.
    if isinstance(quote, list):
        quote = quote[0] if quote else {}

    return quote

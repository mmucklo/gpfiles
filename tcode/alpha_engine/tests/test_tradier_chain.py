"""
Unit tests for ingestion/tradier_chain.py.

All tests mock the Tradier HTTP API — no network calls.
Tests cover:
  - get_expirations: normal response, single-date response, empty response
  - get_chain: full row parsing, single-option response, greeks mapping,
               empty response, missing greeks (illiquid)
  - get_quotes: normal response, multi-symbol list response, empty response
  - Error handling: 401 raises immediately, 429 retries, 5xx retries
  - OptionRow mapping via options_chain._fetch_chain_tradier
"""
import sys
import os
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")

import ingestion.tradier_chain as tc


# ── Fixtures ──────────────────────────────────────────────────────────────────

EXPIRY_RESPONSE = {
    "expirations": {
        "date": ["2026-04-17", "2026-04-24", "2026-05-01", "2026-05-16"]
    }
}

CHAIN_RESPONSE = {
    "options": {
        "option": [
            {
                "symbol": "TSLA260417C00250000",
                "description": "TSLA Apr 17 2026 $250.00 Call",
                "exch": "OPR",
                "type": "option",
                "last": 115.0,
                "change": 2.5,
                "volume": 1200,
                "open": 112.0,
                "high": 118.0,
                "low": 110.0,
                "close": 112.5,
                "bid": 114.5,
                "ask": 115.5,
                "underlying": "TSLA",
                "strike": 250.0,
                "change_percentage": 2.2,
                "average_volume": 800,
                "last_volume": 5,
                "trade_date": 1713300000000,
                "prevclose": 112.5,
                "week_52_high": 150.0,
                "week_52_low": 2.0,
                "bidsize": 10,
                "bidexch": "C",
                "bid_date": 1713300001000,
                "asksize": 10,
                "askexch": "X",
                "ask_date": 1713300001000,
                "open_interest": 5000,
                "contract_size": 100,
                "expiration_date": "2026-04-17",
                "expiration_type": "standard",
                "option_type": "call",
                "root_symbol": "TSLA",
                "greeks": {
                    "delta": 0.8542,
                    "gamma": 0.0023,
                    "theta": -0.1234,
                    "vega": 0.4512,
                    "rho": 0.1234,
                    "phi": -0.0987,
                    "bid_iv": 0.4501,
                    "mid_iv": 0.4550,
                    "ask_iv": 0.4599,
                    "smv_vol": 0.4530,
                    "updated_at": "2026-04-15 10:30:00",
                },
            },
            {
                "symbol": "TSLA260417P00250000",
                "description": "TSLA Apr 17 2026 $250.00 Put",
                "exch": "OPR",
                "type": "option",
                "last": 0.05,
                "change": 0.0,
                "volume": 10,
                "open": 0.05,
                "high": 0.06,
                "low": 0.04,
                "close": 0.05,
                "bid": 0.04,
                "ask": 0.06,
                "underlying": "TSLA",
                "strike": 250.0,
                "change_percentage": 0.0,
                "average_volume": 5,
                "last_volume": 1,
                "trade_date": 1713300000000,
                "prevclose": 0.05,
                "week_52_high": 5.0,
                "week_52_low": 0.01,
                "bidsize": 1,
                "bidexch": "C",
                "bid_date": 1713300001000,
                "asksize": 1,
                "askexch": "X",
                "ask_date": 1713300001000,
                "open_interest": 200,
                "contract_size": 100,
                "expiration_date": "2026-04-17",
                "expiration_type": "standard",
                "option_type": "put",
                "root_symbol": "TSLA",
                "greeks": None,  # illiquid — Tradier returns null greeks
            },
        ]
    }
}

QUOTE_RESPONSE = {
    "quotes": {
        "quote": {
            "symbol": "TSLA",
            "description": "Tesla Inc",
            "exch": "Q",
            "type": "stock",
            "last": 364.20,
            "change": -3.45,
            "volume": 45000000,
            "open": 367.65,
            "high": 368.90,
            "low": 361.10,
            "close": 367.65,
            "bid": 364.19,
            "ask": 364.21,
            "change_percentage": -0.94,
            "average_volume": 55000000,
            "last_volume": 100,
            "trade_date": 1713300000000,
            "prevclose": 367.65,
            "week_52_high": 488.54,
            "week_52_low": 182.00,
        }
    }
}


def _mock_response(status_code: int, json_data: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        from requests import HTTPError
        resp.raise_for_status.side_effect = HTTPError(f"HTTP {status_code}")
    return resp


# ── get_expirations ────────────────────────────────────────────────────────────

class TestGetExpirations:
    def test_normal_response(self, monkeypatch):
        monkeypatch.setenv("TRADIER_API_TOKEN", "test_token")
        with patch("requests.get", return_value=_mock_response(200, EXPIRY_RESPONSE)):
            dates = tc.get_expirations("TSLA")
        assert dates == ["2026-04-17", "2026-04-24", "2026-05-01", "2026-05-16"]

    def test_single_date_string(self, monkeypatch):
        """API may return a single string instead of a list when only 1 expiry."""
        monkeypatch.setenv("TRADIER_API_TOKEN", "test_token")
        single = {"expirations": {"date": "2026-04-17"}}
        with patch("requests.get", return_value=_mock_response(200, single)):
            dates = tc.get_expirations("TSLA")
        assert dates == ["2026-04-17"]

    def test_empty_expirations(self, monkeypatch):
        monkeypatch.setenv("TRADIER_API_TOKEN", "test_token")
        empty = {"expirations": {"date": []}}
        with patch("requests.get", return_value=_mock_response(200, empty)):
            dates = tc.get_expirations("TSLA")
        assert dates == []

    def test_missing_token_raises(self, monkeypatch):
        monkeypatch.setenv("TRADIER_API_TOKEN", "")
        with pytest.raises(RuntimeError, match="TRADIER_API_TOKEN"):
            tc.get_expirations("TSLA")

    def test_401_raises_immediately(self, monkeypatch):
        monkeypatch.setenv("TRADIER_API_TOKEN", "bad_token")
        resp = _mock_response(401, {"fault": {"faultstring": "Invalid ApiKey"}})
        resp.raise_for_status = MagicMock()  # don't raise — our code checks status_code
        with patch("requests.get", return_value=resp):
            with pytest.raises(RuntimeError, match="401"):
                tc.get_expirations("TSLA")

    def test_429_retries(self, monkeypatch):
        monkeypatch.setenv("TRADIER_API_TOKEN", "test_token")
        rate_resp = _mock_response(429, {})
        rate_resp.raise_for_status = MagicMock()
        ok_resp = _mock_response(200, EXPIRY_RESPONSE)
        with patch("requests.get", side_effect=[rate_resp, ok_resp]):
            with patch("time.sleep"):
                dates = tc.get_expirations("TSLA")
        assert len(dates) == 4

    def test_5xx_retries_then_succeeds(self, monkeypatch):
        monkeypatch.setenv("TRADIER_API_TOKEN", "test_token")
        err_resp = _mock_response(503, {})
        err_resp.raise_for_status = MagicMock()
        ok_resp = _mock_response(200, EXPIRY_RESPONSE)
        with patch("requests.get", side_effect=[err_resp, ok_resp]):
            with patch("time.sleep"):
                dates = tc.get_expirations("TSLA")
        assert dates[0] == "2026-04-17"


# ── get_chain ─────────────────────────────────────────────────────────────────

class TestGetChain:
    def test_normal_response_count(self, monkeypatch):
        monkeypatch.setenv("TRADIER_API_TOKEN", "test_token")
        with patch("requests.get", return_value=_mock_response(200, CHAIN_RESPONSE)):
            opts = tc.get_chain("TSLA", "2026-04-17")
        assert len(opts) == 2

    def test_greeks_present_on_call(self, monkeypatch):
        monkeypatch.setenv("TRADIER_API_TOKEN", "test_token")
        with patch("requests.get", return_value=_mock_response(200, CHAIN_RESPONSE)):
            opts = tc.get_chain("TSLA", "2026-04-17")
        call = next(o for o in opts if o["option_type"] == "call")
        assert call["greeks"]["delta"] == pytest.approx(0.8542)
        assert call["greeks"]["gamma"] == pytest.approx(0.0023)
        assert call["greeks"]["theta"] == pytest.approx(-0.1234)
        assert call["greeks"]["vega"]  == pytest.approx(0.4512)
        assert call["greeks"]["mid_iv"] == pytest.approx(0.4550)

    def test_null_greeks_on_illiquid_put(self, monkeypatch):
        monkeypatch.setenv("TRADIER_API_TOKEN", "test_token")
        with patch("requests.get", return_value=_mock_response(200, CHAIN_RESPONSE)):
            opts = tc.get_chain("TSLA", "2026-04-17")
        put = next(o for o in opts if o["option_type"] == "put")
        assert put.get("greeks") is None

    def test_single_option_dict_coerced_to_list(self, monkeypatch):
        """API returns a dict (not list) when only 1 contract exists."""
        monkeypatch.setenv("TRADIER_API_TOKEN", "test_token")
        single = {
            "options": {
                "option": CHAIN_RESPONSE["options"]["option"][0]  # dict, not list
            }
        }
        with patch("requests.get", return_value=_mock_response(200, single)):
            opts = tc.get_chain("TSLA", "2026-04-17")
        assert len(opts) == 1

    def test_empty_chain_returns_empty_list(self, monkeypatch):
        monkeypatch.setenv("TRADIER_API_TOKEN", "test_token")
        empty = {"options": {"option": []}}
        with patch("requests.get", return_value=_mock_response(200, empty)):
            opts = tc.get_chain("TSLA", "2026-04-17")
        assert opts == []

    def test_null_options_key_returns_empty_list(self, monkeypatch):
        monkeypatch.setenv("TRADIER_API_TOKEN", "test_token")
        null_resp = {"options": None}
        with patch("requests.get", return_value=_mock_response(200, null_resp)):
            opts = tc.get_chain("TSLA", "2026-04-17")
        assert opts == []

    def test_401_raises_immediately(self, monkeypatch):
        monkeypatch.setenv("TRADIER_API_TOKEN", "bad_token")
        resp = _mock_response(401, {})
        resp.raise_for_status = MagicMock()
        with patch("requests.get", return_value=resp):
            with pytest.raises(RuntimeError, match="401"):
                tc.get_chain("TSLA", "2026-04-17")


# ── get_quotes ────────────────────────────────────────────────────────────────

class TestGetQuotes:
    def test_normal_response(self, monkeypatch):
        monkeypatch.setenv("TRADIER_API_TOKEN", "test_token")
        with patch("requests.get", return_value=_mock_response(200, QUOTE_RESPONSE)):
            quote = tc.get_quotes("TSLA")
        assert quote["last"] == pytest.approx(364.20)
        assert quote["bid"] == pytest.approx(364.19)
        assert quote["ask"] == pytest.approx(364.21)

    def test_list_response_returns_first(self, monkeypatch):
        """When multiple symbols are returned, use the first."""
        monkeypatch.setenv("TRADIER_API_TOKEN", "test_token")
        list_resp = {"quotes": {"quote": [QUOTE_RESPONSE["quotes"]["quote"]]}}
        with patch("requests.get", return_value=_mock_response(200, list_resp)):
            quote = tc.get_quotes("TSLA")
        assert quote["last"] == pytest.approx(364.20)

    def test_empty_response_returns_empty_dict(self, monkeypatch):
        monkeypatch.setenv("TRADIER_API_TOKEN", "test_token")
        empty = {"quotes": {"quote": {}}}
        with patch("requests.get", return_value=_mock_response(200, empty)):
            quote = tc.get_quotes("TSLA")
        assert isinstance(quote, dict)

    def test_5xx_returns_empty_dict_not_raises(self, monkeypatch):
        """get_quotes failures are non-fatal; returns empty dict."""
        monkeypatch.setenv("TRADIER_API_TOKEN", "test_token")
        err_resp = _mock_response(503, {})
        err_resp.raise_for_status = MagicMock()
        with patch("requests.get", return_value=err_resp):
            with patch("time.sleep"):
                quote = tc.get_quotes("TSLA")
        assert quote == {}

    def test_401_raises(self, monkeypatch):
        """get_quotes should re-raise auth failures."""
        monkeypatch.setenv("TRADIER_API_TOKEN", "bad")
        resp = _mock_response(401, {})
        resp.raise_for_status = MagicMock()
        with patch("requests.get", return_value=resp):
            with pytest.raises(RuntimeError, match="401"):
                tc.get_quotes("TSLA")


# ── OptionRow mapping via _fetch_chain_tradier ────────────────────────────────

class TestFetchChainTradierMapping:
    """Tests for options_chain.OptionsChainCache._fetch_chain_tradier."""

    def setup_method(self):
        from ingestion.options_chain import OptionsChainCache
        self.cache = OptionsChainCache("TSLA")

    def test_call_greeks_source_tradier(self, monkeypatch):
        monkeypatch.setenv("TRADIER_API_TOKEN", "test_token")
        with patch("requests.get", return_value=_mock_response(200, CHAIN_RESPONSE)):
            rows = self.cache._fetch_chain_tradier("2026-04-17")
        calls = [r for r in rows if r.option_type == "CALL"]
        assert len(calls) == 1
        assert calls[0].greeks_source == "tradier"
        assert calls[0].delta == pytest.approx(0.8542)
        assert calls[0].gamma == pytest.approx(0.0023)
        assert calls[0].theta == pytest.approx(-0.1234)
        assert calls[0].vega  == pytest.approx(0.4512)

    def test_call_implied_volatility_from_mid_iv(self, monkeypatch):
        monkeypatch.setenv("TRADIER_API_TOKEN", "test_token")
        with patch("requests.get", return_value=_mock_response(200, CHAIN_RESPONSE)):
            rows = self.cache._fetch_chain_tradier("2026-04-17")
        call = next(r for r in rows if r.option_type == "CALL")
        assert call.implied_volatility == pytest.approx(0.4550)

    def test_put_with_null_greeks_gets_unavailable(self, monkeypatch):
        monkeypatch.setenv("TRADIER_API_TOKEN", "test_token")
        with patch("requests.get", return_value=_mock_response(200, CHAIN_RESPONSE)):
            rows = self.cache._fetch_chain_tradier("2026-04-17")
        put = next(r for r in rows if r.option_type == "PUT")
        assert put.greeks_source == "unavailable"
        assert put.delta is None
        assert put.gamma is None
        assert put.theta is None
        assert put.vega  is None

    def test_option_type_normalisation(self, monkeypatch):
        """Tradier 'call'/'put' strings normalised to 'CALL'/'PUT'."""
        monkeypatch.setenv("TRADIER_API_TOKEN", "test_token")
        with patch("requests.get", return_value=_mock_response(200, CHAIN_RESPONSE)):
            rows = self.cache._fetch_chain_tradier("2026-04-17")
        types = {r.option_type for r in rows}
        assert types == {"CALL", "PUT"}

    def test_bid_ask_volume_oi(self, monkeypatch):
        monkeypatch.setenv("TRADIER_API_TOKEN", "test_token")
        with patch("requests.get", return_value=_mock_response(200, CHAIN_RESPONSE)):
            rows = self.cache._fetch_chain_tradier("2026-04-17")
        call = next(r for r in rows if r.option_type == "CALL")
        assert call.bid == pytest.approx(114.5)
        assert call.ask == pytest.approx(115.5)
        assert call.volume == 1200
        assert call.open_interest == 5000
        assert call.strike == pytest.approx(250.0)
        assert call.expiration_date == "2026-04-17"

    def test_returns_empty_on_tradier_failure(self, monkeypatch):
        """Non-auth Tradier failure returns empty list (fallback applies)."""
        monkeypatch.setenv("TRADIER_API_TOKEN", "test_token")
        err_resp = _mock_response(503, {})
        err_resp.raise_for_status = MagicMock()
        with patch("requests.get", return_value=err_resp):
            with patch("time.sleep"):
                rows = self.cache._fetch_chain_tradier("2026-04-17")
        assert rows == []

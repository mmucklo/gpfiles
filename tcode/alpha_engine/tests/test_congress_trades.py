"""
Tests for alpha_engine/ingestion/congress_trades.py

Covers:
  - Committee weighting (2x for Senate Commerce / House Energy & Commerce)
  - 48-hour window logic (recent vs stale filings)
  - Amount threshold ($15,001 materiality)
  - House XML parsing (mock XML responses)
  - Senate JSON parsing (mock JSON responses)
  - Sentiment multiplier: ×1.15 buy, ×0.85 sell, ×1.0 neutral
"""
import sys
import json
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/home/builder/src/gpfiles/tcode/alpha_engine")

from ingestion.congress_trades import (
    _committee_weight,
    _is_within_48h,
    _parse_amount_lower_bound,
    _parse_house_xml,
    get_congress_trades,
    _COMMITTEE_MEMBERS_119TH,
)


class TestCommitteeWeight(unittest.TestCase):
    """Committee weight must be 2.0 for key committees, 1.0 for all others."""

    def test_senate_commerce_member_by_name(self):
        self.assertEqual(_committee_weight("Wicker"), 2.0)
        self.assertEqual(_committee_weight("Cantwell"), 2.0)

    def test_house_energy_commerce_member_by_name(self):
        self.assertEqual(_committee_weight("Guthrie"), 2.0)
        self.assertEqual(_committee_weight("Pallone"), 2.0)

    def test_unknown_member_returns_baseline(self):
        self.assertEqual(_committee_weight("Smith"), 1.0)
        self.assertEqual(_committee_weight(""), 1.0)

    def test_committee_field_override_senate_commerce(self):
        self.assertEqual(
            _committee_weight("Smith", "Senate Commerce, Science, and Transportation"),
            2.0,
        )

    def test_committee_field_override_house_energy(self):
        self.assertEqual(
            _committee_weight("Jones", "House Energy and Commerce"),
            2.0,
        )

    def test_committee_field_unrelated(self):
        self.assertEqual(
            _committee_weight("Brown", "Senate Armed Services"),
            1.0,
        )

    def test_partial_committee_name_match(self):
        """Case-insensitive substring match for committee field."""
        self.assertEqual(
            _committee_weight("Unknown", "committee on energy and commerce"),
            2.0,
        )


class TestWithin48Hours(unittest.TestCase):
    """48-hour window gate must reject stale filings and accept fresh ones."""

    def _date_str(self, days_ago: float) -> str:
        dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
        return dt.strftime("%Y-%m-%d")

    def test_today_is_within_48h(self):
        self.assertTrue(_is_within_48h(self._date_str(0)))

    def test_yesterday_is_within_48h(self):
        self.assertTrue(_is_within_48h(self._date_str(1)))

    def test_47h_ago_is_within_48h(self):
        self.assertTrue(_is_within_48h(self._date_str(1.95)))

    def test_3_days_ago_is_stale(self):
        self.assertFalse(_is_within_48h(self._date_str(3)))

    def test_week_old_is_stale(self):
        self.assertFalse(_is_within_48h(self._date_str(7)))

    def test_empty_string_returns_false(self):
        self.assertFalse(_is_within_48h(""))

    def test_us_date_format(self):
        """MM/DD/YYYY format used in House XML must also be recognized."""
        today = datetime.now(timezone.utc)
        us_fmt = today.strftime("%m/%d/%Y")
        self.assertTrue(_is_within_48h(us_fmt))


class TestAmountParsing(unittest.TestCase):
    """Amount lower-bound extraction must handle standard disclosure range strings."""

    def test_standard_range(self):
        self.assertEqual(_parse_amount_lower_bound("$15,001 - $50,000"), 15001)

    def test_large_range(self):
        self.assertEqual(_parse_amount_lower_bound("$100,001 - $250,000"), 100001)

    def test_no_range_marker(self):
        self.assertEqual(_parse_amount_lower_bound("$50,000"), 50000)

    def test_empty_returns_zero(self):
        self.assertEqual(_parse_amount_lower_bound(""), 0)

    def test_unparseable_returns_zero(self):
        self.assertEqual(_parse_amount_lower_bound("Unknown"), 0)


class TestHouseXMLParsing(unittest.TestCase):
    """House XML parser must extract TSLA transactions with correct fields."""

    def _now_minus(self, hours: int) -> str:
        dt = datetime.now(timezone.utc) - timedelta(hours=hours)
        return dt.strftime("%m/%d/%Y")

    def _make_xml(self, transactions: list[dict]) -> str:
        """Build a minimal House eFD XML document from transaction dicts."""
        txn_blocks = ""
        for t in transactions:
            txn_blocks += f"""
        <Transaction>
            <FilingDate>{t.get('FilingDate', self._now_minus(0))}</FilingDate>
            <TransactionDate>{t.get('TransactionDate', self._now_minus(2))}</TransactionDate>
            <Asset>{t.get('Asset', 'Tesla, Inc. [TSLA]')}</Asset>
            <TransactionType>{t.get('TransactionType', 'P')}</TransactionType>
            <Amount>{t.get('Amount', '$15,001 - $50,000')}</Amount>
            <Committee>{t.get('Committee', '')}</Committee>
        </Transaction>"""
        return f"""<?xml version="1.0" encoding="utf-8"?>
<FinancialDisclosure>
    <Member>
        <First>{transactions[0].get('First', 'Jane') if transactions else 'Jane'}</First>
        <Last>{transactions[0].get('Last', 'Smith') if transactions else 'Smith'}</Last>
        <State>TX</State>
        <District>7</District>
    </Member>
    <Transactions>{txn_blocks}
    </Transactions>
</FinancialDisclosure>"""

    def test_parses_tsla_purchase(self):
        xml = self._make_xml([{
            "Last": "Wicker",
            "Asset": "Tesla, Inc. [TSLA]",
            "TransactionType": "P",
            "Amount": "$15,001 - $50,000",
            "Committee": "Senate Commerce, Science, and Transportation",
        }])
        trades = _parse_house_xml(xml)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["transaction_type"], "PURCHASE")
        self.assertEqual(trades[0]["amount_lower"], 15001)
        self.assertEqual(trades[0]["ticker"], "TSLA")

    def test_parses_tsla_sale(self):
        xml = self._make_xml([{
            "Asset": "TESLA INC - TSLA",
            "TransactionType": "S",
            "Amount": "$50,001 - $100,000",
        }])
        trades = _parse_house_xml(xml)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["transaction_type"], "SALE")

    def test_filters_non_tsla_assets(self):
        xml = self._make_xml([
            {"Asset": "Apple Inc. [AAPL]", "TransactionType": "P"},
            {"Asset": "Tesla, Inc. [TSLA]", "TransactionType": "P"},
        ])
        # Second transaction has Last from first dict (shared Member block)
        trades = _parse_house_xml(xml)
        # Only TSLA should be returned
        self.assertEqual(len(trades), 1)
        self.assertIn("TSLA", trades[0]["ticker"])

    def test_filters_below_materiality_threshold(self):
        """Amounts below $15,001 must be dropped."""
        xml = self._make_xml([{
            "Asset": "Tesla, Inc. [TSLA]",
            "TransactionType": "P",
            "Amount": "$1,001 - $15,000",  # Below threshold
        }])
        trades = _parse_house_xml(xml)
        self.assertEqual(len(trades), 0)

    def test_committee_weight_applied_from_xml(self):
        """Committee field in XML must result in 2.0× weight for relevant committees."""
        xml = self._make_xml([{
            "Last": "Guthrie",
            "Asset": "Tesla, Inc. [TSLA]",
            "TransactionType": "P",
            "Amount": "$15,001 - $50,000",
            "Committee": "Energy and Commerce",
        }])
        trades = _parse_house_xml(xml)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["committee_weight"], 2.0)

    def test_non_committee_member_gets_baseline_weight(self):
        xml = self._make_xml([{
            "Last": "RandomMember",
            "Asset": "Tesla, Inc. [TSLA]",
            "TransactionType": "P",
            "Amount": "$15,001 - $50,000",
            "Committee": "Senate Armed Services",
        }])
        trades = _parse_house_xml(xml)
        self.assertEqual(trades[0]["committee_weight"], 1.0)

    def test_48h_window_gates_stale_transactions(self):
        """Transactions older than 48h must have within_48h=False."""
        old_date = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%m/%d/%Y")
        xml = self._make_xml([{
            "Asset": "Tesla, Inc. [TSLA]",
            "FilingDate": old_date,
            "TransactionDate": old_date,
            "TransactionType": "P",
            "Amount": "$50,001 - $100,000",
        }])
        trades = _parse_house_xml(xml)
        self.assertEqual(len(trades), 1)
        self.assertFalse(trades[0]["within_48h"])

    def test_handles_malformed_xml_gracefully(self):
        """Malformed XML must return empty list, not raise."""
        trades = _parse_house_xml("<not valid xml <<< >>>")
        self.assertEqual(trades, [])

    def test_empty_xml_returns_empty_list(self):
        trades = _parse_house_xml("")
        self.assertEqual(trades, [])


class TestSentimentMultiplier(unittest.TestCase):
    """get_congress_trades() must return correct sentiment_multiplier."""

    def _mock_with_trades(self, senate_trades, house_trades):
        import ingestion.congress_trades as ct
        ct._CACHE = None  # force cache miss
        with patch.object(ct, "_fetch_senate_ptrs", return_value=senate_trades), \
             patch.object(ct, "_fetch_house_ptrs", return_value=house_trades):
            return ct.get_congress_trades()

    def _recent_trade(self, txn_type: str, weight: float, amount: int = 50000) -> dict:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return {
            "source": "TEST",
            "name": "Test Member",
            "last_name": "Test",
            "date_filed": today,
            "transaction_type": txn_type,
            "amount": f"${amount:,}",
            "amount_lower": amount,
            "ticker": "TSLA",
            "committee": "",
            "committee_weight": weight,
            "link": "",
            "within_48h": True,
        }

    def test_committee_buy_multiplier_is_1_15(self):
        buy = self._recent_trade("PURCHASE", 2.0)
        result = self._mock_with_trades([buy], [])
        self.assertAlmostEqual(result["sentiment_multiplier"], 1.15, places=2)
        self.assertTrue(result["committee_weighted_buy_48h"])
        self.assertEqual(result["signal"], "BULLISH")

    def test_committee_sell_multiplier_is_0_85(self):
        sell = self._recent_trade("SALE", 2.0)
        result = self._mock_with_trades([sell], [])
        self.assertAlmostEqual(result["sentiment_multiplier"], 0.85, places=2)
        self.assertTrue(result["committee_weighted_sell_48h"])
        self.assertEqual(result["signal"], "BEARISH")

    def test_no_recent_trades_neutral(self):
        stale_date = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
        stale = {
            "source": "TEST", "name": "Old Member", "last_name": "Old",
            "date_filed": stale_date, "transaction_type": "PURCHASE",
            "amount": "$50,000", "amount_lower": 50000,
            "ticker": "TSLA", "committee": "", "committee_weight": 2.0,
            "link": "", "within_48h": False,
        }
        result = self._mock_with_trades([stale], [])
        self.assertAlmostEqual(result["sentiment_multiplier"], 1.0, places=2)
        self.assertEqual(result["signal"], "NEUTRAL")

    def test_buy_and_sell_same_48h_returns_neutral(self):
        buy  = self._recent_trade("PURCHASE", 2.0)
        sell = self._recent_trade("SALE", 2.0)
        result = self._mock_with_trades([buy, sell], [])
        self.assertAlmostEqual(result["sentiment_multiplier"], 1.0, places=2)
        self.assertEqual(result["signal"], "NEUTRAL")

    def test_cache_is_1_hour(self):
        """Second call within TTL must return cached result without re-fetching."""
        import ingestion.congress_trades as ct
        ct._CACHE = None
        ct._CACHE_TS = 0.0
        with patch.object(ct, "_fetch_senate_ptrs", return_value=[]) as mock_senate, \
             patch.object(ct, "_fetch_house_ptrs", return_value=[]) as mock_house:
            ct.get_congress_trades()
            ct.get_congress_trades()
            # Both fetchers should only be called once despite 2 get_congress_trades() calls
            self.assertEqual(mock_senate.call_count, 1)
            self.assertEqual(mock_house.call_count, 1)


if __name__ == "__main__":
    unittest.main()

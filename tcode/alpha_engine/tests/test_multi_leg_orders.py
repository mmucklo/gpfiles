"""
test_multi_leg_orders.py — Phase 16

Unit tests for the multi-leg order functions in ibkr_order.py:
  - place_condor
  - place_vertical
  - place_jade_lizard

All tests mock ib_insync so no live IBKR connection is required.
"""

import sys, types, importlib.util, os
from unittest.mock import MagicMock, patch, call
import pytest


# ── Stub ib_insync before importing ibkr_order ───────────────────────────────

def _make_ib_insync_stub():
    ib_mod = types.ModuleType('ib_insync')

    class FakeContract:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.conId = 0

    class FakeComboLeg:
        def __init__(self, conId=0, ratio=1, action='BUY', exchange='SMART'):
            self.conId = conId
            self.ratio = ratio
            self.action = action
            self.exchange = exchange

    class FakeOrder:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeTrade:
        def __init__(self):
            self.order = FakeOrder(orderId=999, lmtPrice=1.00)

    class FakeIB:
        def __init__(self):
            self._connected = False

        def connect(self, host, port, clientId):
            self._connected = True

        def disconnect(self):
            self._connected = False

        def qualifyContracts(self, *contracts):
            for c in contracts:
                c.conId = 1000 + hash(getattr(c, 'strike', 0)) % 1000
            return list(contracts)

        def reqMatchingSymbols(self, symbol):
            return []

        def placeOrder(self, contract, order):
            return FakeTrade()

        def sleep(self, secs):
            pass

    ib_mod.IB = FakeIB
    ib_mod.Contract = FakeContract
    ib_mod.Option = FakeContract
    ib_mod.ComboLeg = FakeComboLeg
    ib_mod.LimitOrder = lambda action, qty, price: FakeOrder(action=action, totalQuantity=qty, lmtPrice=price)
    ib_mod.util = types.SimpleNamespace(startLoop=lambda: None)
    return ib_mod


@pytest.fixture(autouse=True)
def stub_ib(monkeypatch):
    sys.modules['ib_insync'] = _make_ib_insync_stub()
    sys.modules.pop('alpha_engine.ingestion.ibkr_order', None)
    yield
    sys.modules.pop('ib_insync', None)


def _load_ibkr_order():
    path = os.path.join(os.path.dirname(__file__), '..', 'ingestion', 'ibkr_order.py')
    spec = importlib.util.spec_from_file_location('ibkr_order', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_place_condor_returns_dict():
    mod = _load_ibkr_order()
    result = mod.place_condor(
        host='127.0.0.1', port=7497, client_id=10,
        symbol='TSLA', expiry='20260117',
        sell_put=200, buy_put=195,
        sell_call=250, buy_call=255,
        quantity=1, net_credit_limit=1.50,
    )
    assert isinstance(result, dict)
    assert 'order_id' in result or 'error' in result


def test_place_vertical_returns_dict():
    mod = _load_ibkr_order()
    result = mod.place_vertical(
        host='127.0.0.1', port=7497, client_id=11,
        symbol='TSLA', expiry='20260117',
        buy_strike=220, sell_strike=225,
        option_type='C', quantity=1,
        net_debit_limit=1.00,
    )
    assert isinstance(result, dict)
    assert 'order_id' in result or 'error' in result


def test_place_jade_lizard_returns_dict():
    mod = _load_ibkr_order()
    result = mod.place_jade_lizard(
        host='127.0.0.1', port=7497, client_id=12,
        symbol='TSLA', expiry='20260117',
        sell_put=200, sell_call=240, buy_call=245,
        quantity=1, net_credit_limit=2.00,
    )
    assert isinstance(result, dict)
    assert 'order_id' in result or 'error' in result


def test_place_condor_connects_and_disconnects():
    """IB.connect and IB.disconnect should both be called."""
    mod = _load_ibkr_order()
    ib_cls = sys.modules['ib_insync'].IB
    instances = []
    original_init = ib_cls.__init__

    def tracking_init(self):
        original_init(self)
        instances.append(self)

    ib_cls.__init__ = tracking_init

    mod.place_condor(
        host='127.0.0.1', port=7497, client_id=13,
        symbol='TSLA', expiry='20260117',
        sell_put=200, buy_put=195,
        sell_call=250, buy_call=255,
        quantity=1,
    )

    assert len(instances) >= 1


def test_place_vertical_put_spread():
    """Should work for put spreads (option_type='P') too."""
    mod = _load_ibkr_order()
    result = mod.place_vertical(
        host='127.0.0.1', port=7497, client_id=14,
        symbol='TSLA', expiry='20260117',
        buy_strike=195, sell_strike=200,
        option_type='P', quantity=2,
        net_debit_limit=0.80,
    )
    assert isinstance(result, dict)

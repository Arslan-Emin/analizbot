"""BinanceSpotExecutor testleri — sahte (mock) ccxt borsası enjekte edilir, ağsız.

Gerçek ccxt çağrıları yapılmaz; emir mantığı + mutabakat + min-notional + hata
yolları sahte borsayla doğrulanır.
"""

from __future__ import annotations

import pytest

from src.execution.binance_spot import BinanceSpotExecutor
from src.execution.models import ExecMode, OrderSide, PositionState


class FakeExchange:
    """ccxt.binance taklidi: emirleri kaydeder, ayarlanabilir bakiye/fiyat döndürür."""

    def __init__(self, *, price=100.0, free_quote=1000.0, base_free=0.0, min_cost=10.0):
        self.price = price
        self.balances = {"USDT": free_quote, "BTC": base_free}
        self.min_cost = min_cost
        self._oid = 0
        self.created_orders: list[dict] = []
        self.canceled: list[str] = []
        self.order_status: dict[str, dict] = {}
        self.raise_on_buy = False

    def _next_id(self) -> str:
        self._oid += 1
        return f"oid-{self._oid}"

    def fetch_ticker(self, symbol):
        return {"last": self.price}

    def fetch_balance(self):
        return {k: {"free": v} for k, v in self.balances.items()}

    def market(self, symbol):
        return {"limits": {"cost": {"min": self.min_cost}}}

    def amount_to_precision(self, symbol, amount):
        return f"{float(amount):.6f}"

    def price_to_precision(self, symbol, price):
        return f"{float(price):.2f}"

    def create_market_buy_order_with_cost(self, symbol, cost):
        if self.raise_on_buy:
            raise RuntimeError("borsa reddetti")
        qty = cost / self.price
        oid = self._next_id()
        return {"id": oid, "filled": qty, "average": self.price, "cost": cost, "amount": qty}

    def create_market_sell_order(self, symbol, amount):
        oid = self._next_id()
        return {"id": oid, "filled": amount, "average": self.price, "cost": amount * self.price}

    def create_order(self, symbol, type, side, amount, price, params=None):
        oid = self._next_id()
        order = {"id": oid, "symbol": symbol, "type": type, "side": side,
                 "amount": amount, "price": price, "params": params or {}, "status": "open"}
        self.created_orders.append(order)
        return order

    def cancel_order(self, order_id, symbol):
        self.canceled.append(order_id)

    def fetch_order(self, order_id, symbol):
        return self.order_status.get(order_id, {"id": order_id, "status": "open"})


def _ex(**kw):
    fake = FakeExchange(**kw)
    ex = BinanceSpotExecutor("k", "s", testnet=True, quote="USDT", exchange=fake)
    return ex, fake


def test_mode_from_testnet_flag():
    ex, _ = _ex()
    assert ex.mode == ExecMode.TESTNET
    fake = FakeExchange()
    live = BinanceSpotExecutor("k", "s", testnet=False, exchange=fake)
    assert live.mode == ExecMode.LIVE


def test_free_quote_reads_balance():
    ex, _ = _ex(free_quote=1234.5)
    assert ex.free_quote() == 1234.5


def test_buy_places_market_order():
    ex, fake = _ex(price=100.0)
    res = ex.buy("BTC/USDT", 200.0)
    assert res.status == "filled" and res.side == OrderSide.BUY
    assert res.qty == 2.0 and res.fill_price == 100.0 and res.quote_spent == 200.0
    assert res.exchange_order_id is not None


def test_buy_rejected_below_exchange_min_notional():
    ex, _ = _ex(min_cost=50.0)
    res = ex.buy("BTC/USDT", 20.0)
    assert res.status == "rejected" and "min-notional" in res.error


def test_buy_error_is_captured():
    ex, fake = _ex()
    fake.raise_on_buy = True
    res = ex.buy("BTC/USDT", 200.0)
    assert res.status == "error" and "borsa reddetti" in res.error


def test_sell_all_places_market_sell():
    ex, fake = _ex(price=110.0)
    res = ex.sell_all("BTC/USDT", 2.0)
    assert res.status == "filled" and res.side == OrderSide.SELL
    assert res.qty == 2.0 and res.fill_price == 110.0


def test_protective_stop_uses_stop_loss_limit():
    ex, fake = _ex()
    res = ex.place_protective_stop("BTC/USDT", 2.0, 95.0)
    assert res.status == "open" and res.type == "stop_loss_limit"
    assert res.exchange_order_id is not None
    order = fake.created_orders[-1]
    assert order["type"] == "STOP_LOSS_LIMIT" and order["side"] == "sell"
    assert order["params"]["stopPrice"] == pytest.approx(95.0, abs=0.5)
    # Limit fiyat tetiğin altında (dolum garantisi).
    assert order["price"] < 95.0


def test_cancel_order():
    ex, fake = _ex()
    assert ex.cancel("BTC/USDT", "oid-7") is True
    assert "oid-7" in fake.canceled
    assert ex.cancel("BTC/USDT", None) is False


def test_poll_exit_when_stop_filled():
    ex, fake = _ex()
    fake.order_status["oid-1"] = {"id": "oid-1", "status": "closed", "average": 95.0}
    pos = PositionState(symbol="BTC/USDT", entry_price=100.0, qty=2.0, stop_price=95.0,
                        protective_order_id="oid-1", mode=ExecMode.TESTNET)
    fill = ex.poll_protective_exit(pos)
    assert fill is not None and fill.reason == "stop" and fill.price == 95.0


def test_poll_exit_none_when_stop_open():
    ex, fake = _ex(base_free=2.0)
    fake.order_status["oid-1"] = {"id": "oid-1", "status": "open"}
    pos = PositionState(symbol="BTC/USDT", entry_price=100.0, qty=2.0, stop_price=95.0,
                        protective_order_id="oid-1", mode=ExecMode.TESTNET)
    assert ex.poll_protective_exit(pos) is None


def test_poll_exit_liquidated_by_balance():
    # Koruyucu emir bilinmiyor + baz bakiye ~0 → dışarıdan kapanmış.
    ex, fake = _ex(base_free=0.0)
    pos = PositionState(symbol="BTC/USDT", entry_price=100.0, qty=2.0, stop_price=95.0,
                        protective_order_id=None, mode=ExecMode.TESTNET)
    fill = ex.poll_protective_exit(pos)
    assert fill is not None and fill.reason == "liquidated" and fill.price == 95.0

"""PaperExecutor testleri — simüle dolum, ağsız. Giriş/çıkış/%5 stop simülasyonu."""

from __future__ import annotations

import pandas as pd

from src.data.base import MarketDataProvider
from src.execution.models import ExecMode, OrderSide, PositionState
from src.execution.paper import PaperExecutor
from src.storage.db import Repository


class _PriceProvider(MarketDataProvider):
    """Ayarlanabilir anlık fiyat döndüren sahte sağlayıcı."""

    name = "mock"
    market = "crypto"

    def __init__(self, price: float) -> None:
        self.price = price

    def fetch_ohlcv(self, symbol, timeframe="1h", limit=500):
        return pd.DataFrame()

    def get_ticker(self, symbol):
        return self.price

    def list_symbols(self):
        return ["BTC/USDT"]


def _repo(tmp_path):
    return Repository(f"sqlite:///{(tmp_path / 'paper.db').as_posix()}")


def test_buy_fills_at_live_price(tmp_path):
    prov = _PriceProvider(100.0)
    ex = PaperExecutor(prov, _repo(tmp_path), paper_capital=1000.0)
    res = ex.buy("BTC/USDT", 200.0)
    assert res.status == "filled" and res.side == OrderSide.BUY
    assert res.qty == 2.0 and res.fill_price == 100.0 and res.quote_spent == 200.0
    assert res.mode == ExecMode.PAPER


def test_sell_all_fills(tmp_path):
    prov = _PriceProvider(110.0)
    ex = PaperExecutor(prov, _repo(tmp_path), paper_capital=1000.0)
    res = ex.sell_all("BTC/USDT", 2.0)
    assert res.status == "filled" and res.side == OrderSide.SELL
    assert res.qty == 2.0 and res.fill_price == 110.0


def test_free_quote_reflects_open_exposure(tmp_path):
    prov = _PriceProvider(100.0)
    repo = _repo(tmp_path)
    ex = PaperExecutor(prov, repo, paper_capital=1000.0)
    assert ex.free_quote() == 1000.0
    repo.save_position(PositionState(
        symbol="BTC/USDT", entry_price=100.0, qty=3.0, mode=ExecMode.PAPER
    ))
    assert ex.free_quote() == 700.0  # 1000 - 300


def test_protective_stop_is_virtual(tmp_path):
    prov = _PriceProvider(100.0)
    ex = PaperExecutor(prov, _repo(tmp_path), paper_capital=1000.0)
    res = ex.place_protective_stop("BTC/USDT", 2.0, 95.0)
    assert res.status == "open" and res.type == "stop_loss_limit"
    assert res.exchange_order_id == "paper-stop-BTC/USDT"


def test_poll_protective_exit_triggers_below_stop(tmp_path):
    prov = _PriceProvider(100.0)
    ex = PaperExecutor(prov, _repo(tmp_path), paper_capital=1000.0)
    pos = PositionState(symbol="BTC/USDT", entry_price=100.0, qty=2.0, stop_price=95.0,
                        mode=ExecMode.PAPER)
    assert ex.poll_protective_exit(pos) is None  # fiyat 100 > stop 95

    prov.price = 94.0
    fill = ex.poll_protective_exit(pos)
    assert fill is not None and fill.reason == "stop" and fill.price == 95.0

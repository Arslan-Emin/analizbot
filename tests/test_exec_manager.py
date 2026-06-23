"""ExecutionManager testleri — paper executor + gerçek repo, ağsız.

Kapsam: BUY→aç, SELL/HOLD→kapat, BUY→no-op; confirm→pending→approve/reject;
reconcile (stop + take-profit); manuel close; panic.
"""

from __future__ import annotations

import pandas as pd

from src.core.models import Action, AnalysisResult, Signal
from src.data.base import MarketDataProvider
from src.execution.manager import ExecutionManager
from src.execution.models import DecisionMode, ExecMode, OrderSide
from src.execution.paper import PaperExecutor
from src.notify.base import Notifier
from src.storage.db import Repository

ECFG = {
    "risk_per_trade_pct": 1.0,
    "stop_loss_pct": 5.0,
    "take_profit_pct": 10.0,
    "max_position_pct": 50,
    "max_total_exposure_pct": 100,
    "max_concurrent_positions": 5,
    "max_daily_loss_pct": 100.0,   # kill-switch'i etkisizleştir (test odağı değil)
    "min_order_usdt": 10,
    "cooldown_minutes": 0,         # cooldown'u kapat (aç/kapat testleri için)
    "allocation_quote_cap": 0,
}


class _PriceProvider(MarketDataProvider):
    name = "mock"
    market = "crypto"

    def __init__(self, price: float) -> None:
        self.price = price

    def fetch_ohlcv(self, symbol, timeframe="1h", limit=500):
        return pd.DataFrame()

    def get_ticker(self, symbol):
        return self.price

    def list_symbols(self):
        return ["BTC/USDT", "ETH/USDT"]


class _CaptureNotifier(Notifier):
    def __init__(self) -> None:
        self.texts: list[str] = []

    def send_signal(self, result) -> None:
        pass

    def send_text(self, text) -> None:
        self.texts.append(text)


def _result(action, *, symbol="BTC/USDT", price=100.0, take_profit=None):
    sig = Signal(
        symbol=symbol, action=action, confidence=0.8, price=price, reasons=["test"],
        suggested_entry=price, stop_loss=None, take_profit=take_profit, timeframe="4h",
    )
    return AnalysisResult(signal=sig, indicators={"last_price": price}, market="crypto",
                          strategy="ensemble")


def _setup(tmp_path, *, price=100.0, decision=DecisionMode.AUTO):
    prov = _PriceProvider(price)
    repo = Repository(f"sqlite:///{(tmp_path / 'mgr.db').as_posix()}")
    ex = PaperExecutor(prov, repo, paper_capital=1000.0)
    notifier = _CaptureNotifier()
    mgr = ExecutionManager(repo, ex, notifier, ECFG, decision=decision, strategy="ensemble")
    return prov, repo, ex, notifier, mgr


def test_buy_opens_position_with_protective_stop(tmp_path):
    prov, repo, ex, notifier, mgr = _setup(tmp_path)
    mgr.on_signal(_result(Action.BUY))

    pos = repo.get_open_position("BTC/USDT", "paper")
    assert pos is not None
    assert pos["qty"] == 2.0          # 200 USDT / 100
    assert pos["entry_price"] == 100.0
    assert pos["stop_price"] == 95.0  # %5
    assert pos["tp_price"] == 110.0   # config %10
    assert pos["protective_order_id"] == "paper-stop-BTC/USDT"

    orders = repo.list_exec_orders(mode="paper")
    types = {o["type"] for o in orders}
    assert "market" in types and "stop_loss_limit" in types
    assert any("AÇILIŞ" in t for t in notifier.texts)


def test_sell_closes_position(tmp_path):
    prov, repo, ex, notifier, mgr = _setup(tmp_path)
    mgr.on_signal(_result(Action.BUY))
    prov.price = 105.0
    mgr.on_signal(_result(Action.SELL, price=105.0))

    assert repo.get_open_position("BTC/USDT", "paper") is None
    closed = repo.list_positions(status="closed", mode="paper")
    assert len(closed) == 1 and closed[0]["pnl_quote"] == 10.0  # (105-100)*2
    assert repo.get_daily_pnl(closed[0]["closed_at"].strftime("%Y-%m-%d"), "paper") == 10.0


def test_hold_closes_position(tmp_path):
    prov, repo, ex, notifier, mgr = _setup(tmp_path)
    mgr.on_signal(_result(Action.BUY))
    mgr.on_signal(_result(Action.HOLD))
    assert repo.get_open_position("BTC/USDT", "paper") is None


def test_buy_when_in_position_is_noop(tmp_path):
    prov, repo, ex, notifier, mgr = _setup(tmp_path)
    mgr.on_signal(_result(Action.BUY))
    orders_before = len(repo.list_exec_orders(mode="paper"))
    mgr.on_signal(_result(Action.BUY))  # zaten long → piramitleme yok
    assert repo.count_open_positions("paper") == 1
    assert len(repo.list_exec_orders(mode="paper")) == orders_before


def test_confirm_mode_creates_pending_then_approve(tmp_path):
    prov, repo, ex, notifier, mgr = _setup(tmp_path, decision=DecisionMode.CONFIRM)
    mgr.on_signal(_result(Action.BUY))

    assert repo.get_open_position("BTC/USDT", "paper") is None  # henüz emir yok
    pending = repo.list_pending_intents("PENDING", "paper")
    assert len(pending) == 1
    assert any("ONAY BEKLİYOR" in t for t in notifier.texts)

    ok, msg = mgr.approve_intent(pending[0]["id"])
    assert ok
    assert repo.get_open_position("BTC/USDT", "paper") is not None
    assert repo.get_pending_intent(pending[0]["id"])["status"] == "EXECUTED"


def test_confirm_mode_reject(tmp_path):
    prov, repo, ex, notifier, mgr = _setup(tmp_path, decision=DecisionMode.CONFIRM)
    mgr.on_signal(_result(Action.BUY))
    iid = repo.list_pending_intents("PENDING", "paper")[0]["id"]

    ok, msg = mgr.reject_intent(iid)
    assert ok
    assert repo.get_pending_intent(iid)["status"] == "REJECTED"
    assert repo.get_open_position("BTC/USDT", "paper") is None


def test_reconcile_protective_stop_closes(tmp_path):
    prov, repo, ex, notifier, mgr = _setup(tmp_path)
    mgr.on_signal(_result(Action.BUY))      # stop 95
    prov.price = 94.0                        # stop'un altına düştü
    mgr.reconcile()

    assert repo.get_open_position("BTC/USDT", "paper") is None
    closed = repo.list_positions(status="closed", mode="paper")[0]
    assert closed["exit_price"] == 95.0 and closed["pnl_quote"] == -10.0  # (95-100)*2


def test_reconcile_take_profit_closes(tmp_path):
    prov, repo, ex, notifier, mgr = _setup(tmp_path)
    mgr.on_signal(_result(Action.BUY, take_profit=110.0))
    prov.price = 111.0                       # TP'ye ulaştı
    mgr.reconcile()

    assert repo.get_open_position("BTC/USDT", "paper") is None
    closed = repo.list_positions(status="closed", mode="paper")[0]
    assert closed["pnl_quote"] == 22.0       # (111-100)*2


def test_manual_close(tmp_path):
    prov, repo, ex, notifier, mgr = _setup(tmp_path)
    mgr.on_signal(_result(Action.BUY))
    ok, msg = mgr.close_symbol("BTC/USDT")
    assert ok and repo.get_open_position("BTC/USDT", "paper") is None
    ok2, _ = mgr.close_symbol("BTC/USDT")
    assert not ok2  # açık pozisyon kalmadı


def test_panic_closes_all_and_rejects_pending(tmp_path):
    prov, repo, ex, notifier, mgr = _setup(tmp_path)
    mgr.on_signal(_result(Action.BUY, symbol="BTC/USDT"))
    mgr.on_signal(_result(Action.BUY, symbol="ETH/USDT"))
    assert repo.count_open_positions("paper") == 2

    # Elle bir bekleyen niyet ekle (panic onu da reddetmeli).
    from src.execution.models import OrderIntent
    repo.save_pending_intent(
        OrderIntent(symbol="SOL/USDT", side=OrderSide.BUY, quote_amount=50.0), ExecMode.PAPER
    )

    msg = mgr.panic()
    assert repo.count_open_positions("paper") == 0
    assert repo.list_pending_intents("PENDING", "paper") == []
    assert "2 pozisyon" in msg

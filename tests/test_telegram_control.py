"""TelegramController testleri — komut→yanıt mantığı, paper executor + repo, ağsız.

PTB (python-telegram-bot) kablolaması test edilmez; saf kontrol mantığı test edilir.
"""

from __future__ import annotations

import pandas as pd

from src.app.telegram_bot import TelegramController
from src.core.models import Action, AnalysisResult, Signal
from src.data.base import MarketDataProvider
from src.execution.manager import ExecutionManager
from src.execution.models import DecisionMode, ExecMode, OrderIntent, OrderSide
from src.execution.paper import PaperExecutor
from src.notify.base import Notifier
from src.storage.db import Repository

ECFG = {
    "enabled": True, "mode": "paper", "decision": "confirm",
    "risk_per_trade_pct": 1.0, "stop_loss_pct": 5.0, "take_profit_pct": 10.0,
    "max_position_pct": 50, "max_total_exposure_pct": 100, "max_concurrent_positions": 5,
    "max_daily_loss_pct": 100.0, "min_order_usdt": 10, "cooldown_minutes": 0,
    "allocation_quote_cap": 0,
}


class _PriceProvider(MarketDataProvider):
    name = "mock"
    market = "crypto"

    def __init__(self, price=100.0):
        self.price = price

    def fetch_ohlcv(self, symbol, timeframe="1h", limit=500):
        return pd.DataFrame()

    def get_ticker(self, symbol):
        return self.price

    def list_symbols(self):
        return ["BTC/USDT"]


class _NullNotifier(Notifier):
    def send_signal(self, result):
        pass

    def send_text(self, text):
        pass


def _result(action, symbol="BTC/USDT", price=100.0):
    sig = Signal(symbol=symbol, action=action, confidence=0.8, price=price,
                 reasons=["t"], suggested_entry=price, timeframe="4h")
    return AnalysisResult(signal=sig, indicators={}, market="crypto", strategy="ensemble")


def _setup(tmp_path, decision=DecisionMode.CONFIRM):
    prov = _PriceProvider()
    repo = Repository(f"sqlite:///{(tmp_path / 'tg.db').as_posix()}")
    ex = PaperExecutor(prov, repo, paper_capital=1000.0)
    mgr = ExecutionManager(repo, ex, _NullNotifier(), ECFG, decision=decision, strategy="ens")
    ctrl = TelegramController(repo, mgr, ECFG)
    return prov, repo, mgr, ctrl


def test_help_and_status(tmp_path):
    prov, repo, mgr, ctrl = _setup(tmp_path)
    assert "/approve" in ctrl.help_text()
    s = ctrl.status_text()
    assert "paper" in s and "Açık pozisyon: 0" in s


def test_pending_approve_flow(tmp_path):
    prov, repo, mgr, ctrl = _setup(tmp_path)
    mgr.on_signal(_result(Action.BUY))           # confirm → pending intent #1
    assert "Bekleyen onay yok" not in ctrl.pending_text()
    iid = repo.list_pending_intents("PENDING", "paper")[0]["id"]

    out = ctrl.approve(str(iid))
    assert "açıldı" in out
    assert repo.get_open_position("BTC/USDT", "paper") is not None

    # Pozisyon listesi anlık fiyat + yüzde PnL göstermeli (giriş 100 → fiyat 110 = +%10).
    prov.price = 110.0
    pos_text = ctrl.positions_text()
    assert "BTC/USDT" in pos_text
    assert "anlık 110" in pos_text and "+10.00%" in pos_text


def test_reject_works_without_manager(tmp_path):
    repo = Repository(f"sqlite:///{(tmp_path / 'tg2.db').as_posix()}")
    iid = repo.save_pending_intent(
        OrderIntent(symbol="ETH/USDT", side=OrderSide.BUY, quote_amount=50.0), ExecMode.PAPER
    )
    ctrl = TelegramController(repo, None, ECFG)   # exec_manager YOK
    out = ctrl.reject(str(iid))
    assert "reddedildi" in out
    assert repo.get_pending_intent(iid)["status"] == "REJECTED"


def test_action_blocked_when_no_manager(tmp_path):
    repo = Repository(f"sqlite:///{(tmp_path / 'tg3.db').as_posix()}")
    ctrl = TelegramController(repo, None, {**ECFG, "enabled": False})
    assert "kullanılamaz" in ctrl.approve("1")
    assert "kullanılamaz" in ctrl.close("BTC/USDT")
    assert "kullanılamaz" in ctrl.panic()
    # izleme komutları yine çalışır
    assert "Açık pozisyon yok" in ctrl.positions_text()


def test_close_and_panic(tmp_path):
    prov, repo, mgr, ctrl = _setup(tmp_path, decision=DecisionMode.AUTO)
    mgr.on_signal(_result(Action.BUY))            # auto → hemen pozisyon
    assert repo.get_open_position("BTC/USDT", "paper") is not None
    out = ctrl.close("btc/usdt")                  # küçük harf de çalışmalı
    assert "kapatıldı" in out
    assert repo.get_open_position("BTC/USDT", "paper") is None


def test_bad_args(tmp_path):
    prov, repo, mgr, ctrl = _setup(tmp_path)
    assert "Kullanım" in ctrl.approve(None)
    assert "Kullanım" in ctrl.approve("abc")
    assert "Kullanım" in ctrl.close(None)

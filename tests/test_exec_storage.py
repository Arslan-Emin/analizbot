"""Emir yürütme depo (Repository) testleri — geçici dosya tabanlı SQLite, ağsız."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.execution.models import ExecMode, OrderIntent, OrderResult, OrderSide, PositionState
from src.storage.db import Repository


def _repo(tmp_path, name="exec.db") -> Repository:
    return Repository(f"sqlite:///{(tmp_path / name).as_posix()}")


def test_exec_order_roundtrip(tmp_path):
    repo = _repo(tmp_path)
    oid = repo.save_exec_order(OrderResult(
        symbol="BTC/USDT", side=OrderSide.BUY, type="market", qty=0.5, price=100.0,
        status="filled", mode=ExecMode.PAPER, fill_price=100.0, quote_spent=50.0,
    ))
    assert oid > 0
    orders = repo.list_exec_orders(symbol="BTC/USDT", mode="paper")
    assert len(orders) == 1
    assert orders[0]["side"] == "BUY"
    assert orders[0]["quote_spent"] == 50.0


def test_position_lifecycle(tmp_path):
    repo = _repo(tmp_path)
    assert repo.get_open_position("BTC/USDT", "paper") is None

    pid = repo.save_position(PositionState(
        symbol="BTC/USDT", entry_price=100.0, qty=2.0, stop_price=95.0, tp_price=110.0,
        mode=ExecMode.PAPER, strategy="ensemble",
    ))
    assert pid > 0
    assert repo.count_open_positions("paper") == 1
    assert repo.open_exposure("paper") == 200.0  # 100 * 2

    pos = repo.get_open_position("BTC/USDT", "paper")
    assert pos["entry_price"] == 100.0 and pos["stop_price"] == 95.0

    # Başka mod karışmamalı.
    assert repo.get_open_position("BTC/USDT", "live") is None

    repo.update_position(pid, status="closed", exit_price=110.0, pnl_quote=20.0,
                         closed_at=datetime.now(UTC))
    assert repo.get_open_position("BTC/USDT", "paper") is None
    assert repo.count_open_positions("paper") == 0
    closed = repo.list_positions(status="closed", mode="paper")
    assert len(closed) == 1 and closed[0]["pnl_quote"] == 20.0


def test_pending_intent_roundtrip(tmp_path):
    repo = _repo(tmp_path)
    iid = repo.save_pending_intent(
        OrderIntent(symbol="ETH/USDT", side=OrderSide.BUY, quote_amount=50.0,
                    stop_price=1900.0, take_profit=2200.0, confidence=0.7, reason="test"),
        ExecMode.PAPER,
    )
    assert iid > 0
    pending = repo.list_pending_intents("PENDING", "paper")
    assert len(pending) == 1 and pending[0]["symbol"] == "ETH/USDT"

    repo.update_pending_intent(iid, status="EXECUTED", resolved_at=datetime.now(UTC))
    assert repo.list_pending_intents("PENDING", "paper") == []
    assert repo.get_pending_intent(iid)["status"] == "EXECUTED"


def test_daily_pnl_upsert(tmp_path):
    repo = _repo(tmp_path)
    day = "2026-06-22"
    assert repo.get_daily_pnl(day, "paper") == 0.0
    repo.add_daily_pnl(day, -5.0, "paper")
    repo.add_daily_pnl(day, 12.0, "paper")
    assert repo.get_daily_pnl(day, "paper") == 7.0
    # Farklı mod ayrı tutulur.
    assert repo.get_daily_pnl(day, "live") == 0.0


def test_last_trade_time(tmp_path):
    repo = _repo(tmp_path)
    assert repo.last_trade_time("BTC/USDT", "paper") is None
    ts = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)
    repo.save_exec_order(OrderResult(
        symbol="BTC/USDT", side=OrderSide.BUY, type="market", qty=1.0, price=100.0,
        status="filled", mode=ExecMode.PAPER, created_at=ts,
    ))
    got = repo.last_trade_time("BTC/USDT", "paper")
    assert got is not None
    # SQLite tz'siz dönebilir; tarih/saat eşleşmeli.
    assert abs(got.replace(tzinfo=UTC) - ts) < timedelta(seconds=1)

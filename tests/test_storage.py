"""Depo (Repository) testleri — geçici dosya tabanlı SQLite, ağsız."""

from __future__ import annotations

from src.core.models import Action, AnalysisResult, Signal
from src.storage.db import Repository


def _make_result(symbol: str, action: Action) -> AnalysisResult:
    sig = Signal(
        symbol=symbol,
        action=action,
        confidence=0.75,
        price=100.0,
        reasons=["test gerekçesi"],
        suggested_entry=100.0,
        stop_loss=98.0,
        take_profit=106.0,
        suggested_size_quote=500.0,
        timeframe="1h",
    )
    return AnalysisResult(signal=sig, indicators={}, market="crypto")


def test_save_signal_and_last_signal(tmp_path):
    db_url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    repo = Repository(db_url)

    assert repo.last_signal_for("BTC/USDT") is None  # henüz kayıt yok

    repo.save_signal(_make_result("BTC/USDT", Action.BUY))
    repo.save_signal(_make_result("BTC/USDT", Action.SELL))  # daha yeni

    # En son aksiyon SELL olmalı
    assert repo.last_signal_for("BTC/USDT") == Action.SELL
    # Başka sembolü etkilememeli
    assert repo.last_signal_for("ETH/USDT") is None


def test_save_run(tmp_path):
    from datetime import UTC, datetime

    db_url = f"sqlite:///{(tmp_path / 'runs.db').as_posix()}"
    repo = Repository(db_url)
    run_id = repo.save_run(
        started_at=datetime(2024, 1, 1, tzinfo=UTC),
        finished_at=datetime(2024, 1, 1, tzinfo=UTC),
        symbols_scanned=3,
        signals_generated=2,
        errors=["BTC/USDT: timeout"],
    )
    assert run_id > 0

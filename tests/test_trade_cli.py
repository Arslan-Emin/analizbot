"""trade CLI sub-app testleri — ağsız yollar (status/positions/pending/reject).

approve/close/panic canlı fiyat (ağ) gerektirir; mantıkları test_exec_manager'da
mock provider ile kapsamlıca test edilir. Burada CLI kablolaması + render + reddet
akışı geçici DB ile doğrulanır.
"""

from __future__ import annotations

from typer.testing import CliRunner

from src.app.cli import app
from src.execution.models import ExecMode, OrderIntent, OrderSide, PositionState
from src.storage.db import Repository

runner = CliRunner()


def _use_temp_db(tmp_path, monkeypatch) -> Repository:
    db = tmp_path / "cli.db"
    monkeypatch.setenv("DB_URL", f"sqlite:///{db.as_posix()}")
    monkeypatch.setenv("COLUMNS", "200")  # rich tablosu dar terminalde sarmasın
    return Repository(f"sqlite:///{db.as_posix()}")


def test_status_runs_empty(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    result = runner.invoke(app, ["trade", "status"])
    assert result.exit_code == 0
    assert "paper" in result.stdout  # varsayılan kademe


class _FakeProvider:
    """get_ticker döndüren minimal sahte sağlayıcı (ağsız test için)."""

    def get_ticker(self, symbol):
        return 110.0


def test_positions_lists_open(tmp_path, monkeypatch):
    repo = _use_temp_db(tmp_path, monkeypatch)
    repo.save_position(PositionState(
        symbol="BTC/USDT", entry_price=100.0, qty=2.0, stop_price=95.0, tp_price=110.0,
        mode=ExecMode.PAPER,
    ))
    # Anlık fiyatı ağdan çekmesin: sahte sağlayıcı enjekte et (giriş 100 → fiyat 110 = +%10).
    monkeypatch.setattr("src.app.cli.get_provider", lambda *a, **k: _FakeProvider())
    result = runner.invoke(app, ["trade", "positions"])
    assert result.exit_code == 0
    assert "BTC/USDT" in result.stdout
    assert "+10.00" in result.stdout


def test_pending_and_reject_flow(tmp_path, monkeypatch):
    repo = _use_temp_db(tmp_path, monkeypatch)
    iid = repo.save_pending_intent(
        OrderIntent(symbol="ETH/USDT", side=OrderSide.BUY, quote_amount=50.0,
                    stop_price=1900.0, take_profit=2200.0, confidence=0.7),
        ExecMode.PAPER,
    )
    listed = runner.invoke(app, ["trade", "pending"])
    assert listed.exit_code == 0
    assert "ETH/USDT" in listed.stdout

    rejected = runner.invoke(app, ["trade", "reject", str(iid)])
    assert rejected.exit_code == 0
    assert repo.get_pending_intent(iid)["status"] == "REJECTED"

    # Aynı niyet ikinci kez reddedilemez.
    again = runner.invoke(app, ["trade", "reject", str(iid)])
    assert again.exit_code == 1


def test_reject_missing_intent(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    result = runner.invoke(app, ["trade", "reject", "999"])
    assert result.exit_code == 1
    assert "bulunamadı" in result.stdout

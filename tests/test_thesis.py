"""Tez (thesis) yaşam döngüsü + MAE/MFE + repository testleri — ağsız."""

from __future__ import annotations

from src.learning.thesis import (
    ThesisState,
    can_transition,
    compute_mae_mfe,
    realized_return,
)
from src.storage.db import Repository

# ------------------------------ state machine ------------------------------ #


def test_can_transition_valid():
    assert can_transition("IDEA", "ENTRY_READY")
    assert can_transition(ThesisState.ENTRY_READY, ThesisState.ACTIVE)
    assert can_transition("ACTIVE", "CLOSED")
    assert can_transition("ACTIVE", "INVALIDATED")
    assert can_transition("ENTRY_READY", "IDEA")  # geri alınabilir


def test_can_transition_invalid():
    assert not can_transition("IDEA", "ACTIVE")       # aşama atlanamaz
    assert not can_transition("IDEA", "CLOSED")
    assert not can_transition("CLOSED", "ACTIVE")     # terminal
    assert not can_transition("INVALIDATED", "IDEA")  # terminal


# -------------------------------- MAE / MFE -------------------------------- #


def test_compute_mae_mfe_long():
    # entry 100; en yüksek 120 → MFE +%20; en düşük 90 → MAE -%10.
    mae, mfe = compute_mae_mfe(100, "long", [110, 120, 105], [95, 90, 100])
    assert mfe == 20.0
    assert mae == -10.0


def test_compute_mae_mfe_short():
    # short entry 100; fiyat 80'e düşer (lehte) → MFE +%25; 110'a çıkar (aleyhte) → MAE ~-%9.09.
    mae, mfe = compute_mae_mfe(100, "short", [110, 105], [80, 90])
    assert mfe == 25.0
    assert abs(mae - (-9.09)) < 0.01


def test_compute_mae_mfe_empty():
    assert compute_mae_mfe(0, "long", [], []) == (0.0, 0.0)


def test_realized_return():
    assert realized_return(100, 110, "long") == 10.0
    # short konvansiyonu backtest ile aynı: entry/exit-1. 100→90 cover → +%11.11.
    assert realized_return(100, 90, "short") == round((100 / 90 - 1) * 100, 4)
    assert realized_return(100, 110, "short") == round((100 / 110 - 1) * 100, 4)  # aleyhte


# ------------------------------- repository -------------------------------- #


def test_thesis_lifecycle(tmp_path):
    db = f"sqlite:///{(tmp_path / 'theses.db').as_posix()}"
    repo = Repository(db)

    tid = repo.create_thesis(
        "BTC/USDT", "long", "200-EMA üstü kırılım", entry_price=100, stop_loss=95, take_profit=110
    )
    t = repo.get_thesis(tid)
    assert t["state"] == "IDEA"
    assert t["symbol"] == "BTC/USDT"
    assert t["entry_price"] == 100

    repo.update_thesis(tid, state="ENTRY_READY")
    assert repo.get_thesis(tid)["state"] == "ENTRY_READY"

    repo.update_thesis(tid, state="ACTIVE")
    assert len(repo.list_theses(state="ACTIVE")) == 1
    assert len(repo.list_theses(state="IDEA")) == 0

    repo.update_thesis(
        tid, state="CLOSED", exit_price=108, realized_return_pct=8.0, mae_pct=-2.0, mfe_pct=9.0
    )
    closed = repo.get_thesis(tid)
    assert closed["state"] == "CLOSED"
    assert closed["realized_return_pct"] == 8.0
    assert closed["mae_pct"] == -2.0


def test_get_missing_thesis(tmp_path):
    repo = Repository(f"sqlite:///{(tmp_path / 'x.db').as_posix()}")
    assert repo.get_thesis(999) is None
    assert repo.list_theses() == []

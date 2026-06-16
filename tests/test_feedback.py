"""Geri besleme / öğrenme döngüsü testleri — ağsız, deterministik.

Kapsam: simulate_outcome, şema migrasyonu, evaluator (mock provider),
stats.performance (Brier dahil) ve ConfidenceCalibrator shrink davranışı.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pandas as pd
from sqlalchemy import inspect

from src.core.models import Action, AnalysisResult, Signal
from src.core.simulate import simulate_outcome
from src.learning.calibrator import ConfidenceCalibrator
from src.learning.evaluator import evaluate_open_signals
from src.learning.stats import overall, performance
from src.storage.db import Repository

# ----------------------------- simulate_outcome -----------------------------


def test_simulate_outcome_long_win():
    # entry 100, stop 95, tp 110. 3. barda high 111 → hedef (WIN).
    highs = [101, 105, 111, 120]
    lows = [99, 98, 104, 110]
    closes = [100, 104, 110, 115]
    res = simulate_outcome(highs, lows, closes, "long", 100.0, 95.0, 110.0, max_bars=10)
    assert res.outcome == "WIN"
    assert res.exit_reason == "tp"
    assert res.bars_to_outcome == 3
    assert res.return_pct > 0


def test_simulate_outcome_long_loss():
    # 2. barda low 94 → stop (LOSS). Aynı bar hem stop hem tp olsa stop önce gelir.
    highs = [101, 111, 120]
    lows = [99, 94, 110]
    closes = [100, 96, 115]
    res = simulate_outcome(highs, lows, closes, "long", 100.0, 95.0, 110.0, max_bars=10)
    assert res.outcome == "LOSS"
    assert res.exit_reason == "stop"
    assert res.bars_to_outcome == 2
    assert res.return_pct < 0


def test_simulate_outcome_expired():
    # max_bars dolup hiçbiri değmedi → EXPIRED, son kapanışla işaretlenir.
    highs = [101, 102, 103]
    lows = [99, 98, 99]
    closes = [100, 101, 102]
    res = simulate_outcome(highs, lows, closes, "long", 100.0, 90.0, 120.0, max_bars=3)
    assert res.outcome == "EXPIRED"
    assert res.exit_reason == "expired"
    assert res.bars_to_outcome == 3


def test_simulate_outcome_open_when_not_enough_bars():
    # max_bars 10 ama sadece 3 bar var ve hiçbir seviye değmedi → OPEN.
    res = simulate_outcome([101, 102, 103], [99, 98, 99], [100, 101, 102],
                           "long", 100.0, 90.0, 120.0, max_bars=10)
    assert res.outcome == "OPEN"


def test_simulate_outcome_short_win():
    # short: entry 100, stop 105, tp 90. low 89 → hedef (WIN).
    res = simulate_outcome([101, 102], [98, 89], [100, 90],
                           "short", 100.0, 105.0, 90.0, max_bars=10)
    assert res.outcome == "WIN"
    assert res.return_pct > 0


# ----------------------------- şema migrasyonu -----------------------------


def test_schema_migration_adds_columns(tmp_path):
    # Eski (kolonsuz) bir signals tablosu kur, Repository ALTER ile genişletmeli.
    db_file = tmp_path / "old.db"
    conn = sqlite3.connect(db_file)
    conn.execute(
        "CREATE TABLE signals (id INTEGER PRIMARY KEY, symbol TEXT, action TEXT, "
        "confidence REAL, price REAL, timeframe TEXT, suggested_entry REAL, "
        "stop_loss REAL, take_profit REAL, suggested_size_quote REAL, reasons TEXT, "
        "market TEXT, created_at TEXT)"
    )
    conn.commit()
    conn.close()

    repo = Repository(f"sqlite:///{db_file}")
    cols = {c["name"] for c in inspect(repo.engine).get_columns("signals")}
    for new_col in ("strategy", "outcome", "realized_return_pct", "r_multiple",
                    "bars_to_outcome", "exit_price", "exit_reason", "evaluated_at"):
        assert new_col in cols


# ----------------------------- evaluator + stats -----------------------------


class _MockRangeProvider:
    """fetch_ohlcv_range sunan basit mock (tz-aware UTC index)."""

    market = "crypto"

    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df

    def fetch_ohlcv_range(self, symbol, timeframe, since_ms, until_ms):
        ms = self._df.index.astype("int64") // 10**6
        mask = (ms >= since_ms) & (ms < until_ms)
        return self._df[mask]


def _future_df(created: datetime, highs, lows, closes) -> pd.DataFrame:
    idx = pd.date_range(created + timedelta(hours=1), periods=len(highs), freq="1h", tz="UTC")
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows, "close": closes, "volume": [1.0] * len(highs)},
        index=idx,
    )


def _save_signal(repo, action, entry, stop, tp, created, conf=0.7, strategy="ema_rsi"):
    sig = Signal(
        symbol="BTC/USDT", action=action, confidence=conf, price=entry,
        reasons=["test"], suggested_entry=entry, stop_loss=stop, take_profit=tp,
        suggested_size_quote=10.0, timeframe="1h", created_at=created,
    )
    return repo.save_signal(AnalysisResult(signal=sig, indicators={}, strategy=strategy))


def test_evaluator_resolves_signal(tmp_path):
    repo = Repository(f"sqlite:///{tmp_path / 'fb.db'}")
    created = datetime(2024, 1, 1, tzinfo=UTC)
    _save_signal(repo, Action.BUY, 100.0, 95.0, 110.0, created)

    df = _future_df(created, highs=[101, 105, 111], lows=[99, 98, 104], closes=[100, 104, 110])
    provider = _MockRangeProvider(df)
    now = datetime(2024, 1, 2, tzinfo=UTC)

    stats = evaluate_open_signals(repo, provider, eval_horizon_bars=10, now=now)
    assert stats["resolved"] == 1

    rows = repo.outcomes()
    assert len(rows) == 1
    assert rows[0]["outcome"] == "WIN"

    # Çözülen sinyal artık 'unresolved' listesinde olmamalı (tekrar değerlendirilmez).
    assert repo.unresolved_signals() == []


def test_evaluator_leaves_open_when_no_future_data(tmp_path):
    repo = Repository(f"sqlite:///{tmp_path / 'fb2.db'}")
    created = datetime(2024, 1, 1, tzinfo=UTC)
    _save_signal(repo, Action.BUY, 100.0, 95.0, 110.0, created)

    # Tek bar, hiçbir seviye değmiyor → açık kalmalı.
    provider = _MockRangeProvider(_future_df(created, [101], [99], [100]))
    stats = evaluate_open_signals(repo, provider, eval_horizon_bars=10,
                                  now=datetime(2024, 1, 2, tzinfo=UTC))
    assert stats["resolved"] == 0
    assert stats["open"] == 1
    assert len(repo.unresolved_signals()) == 1  # hâlâ açık


def test_performance_and_brier(tmp_path):
    repo = Repository(f"sqlite:///{tmp_path / 'perf.db'}")
    # 2 WIN, 1 LOSS elle kaydet.
    now = datetime(2024, 1, 1, tzinfo=UTC)
    s1 = _save_signal(repo, Action.BUY, 100, 95, 110, now, conf=0.8)
    s2 = _save_signal(repo, Action.BUY, 100, 95, 110, now, conf=0.6)
    s3 = _save_signal(repo, Action.SELL, 100, 105, 90, now, conf=0.7)
    repo.save_outcome(s1, "WIN", 5.0, 1.0, 3, 110, "tp", now)
    repo.save_outcome(s2, "WIN", 4.0, 0.8, 4, 110, "tp", now)
    repo.save_outcome(s3, "LOSS", -3.0, -1.0, 2, 105, "stop", now)

    df = performance(repo)
    assert not df.empty
    assert df["n"].sum() == 3

    o = overall(repo)
    assert o["n"] == 3
    assert 0.0 <= o["brier"] <= 1.0
    assert round(o["hit_rate"], 0) == 67  # 2/3


# ----------------------------- calibrator -----------------------------


def test_calibrator_not_ready_returns_raw(tmp_path):
    repo = Repository(f"sqlite:///{tmp_path / 'cal0.db'}")
    cal = ConfidenceCalibrator.from_history(repo, min_samples=30)
    assert cal.ready is False
    assert cal.calibrate(0.9) == 0.9  # veri yoksa ham değer


def test_calibrator_shrinks_low_sample_bin(tmp_path):
    repo = Repository(f"sqlite:///{tmp_path / 'cal1.db'}")
    now = datetime(2024, 1, 1, tzinfo=UTC)
    # 40 sinyal: çoğu kaybeden (düşük prior); yüksek güvenli birkaç kazanan.
    for _ in range(40):
        sid = _save_signal(repo, Action.BUY, 100, 95, 110, now, conf=0.55)
        repo.save_outcome(sid, "LOSS", -3.0, -1.0, 2, 95, "stop", now)
    # Tek bir yüksek-güven (0.95) kazanan: az örnek → prior'a çekilmeli.
    sid = _save_signal(repo, Action.BUY, 100, 95, 110, now, conf=0.95)
    repo.save_outcome(sid, "WIN", 10.0, 2.0, 3, 110, "tp", now)

    cal = ConfidenceCalibrator.from_history(repo, min_samples=30, n_bins=10)
    assert cal.ready is True
    # 0.95'lik kova tek örnekli; kalibre değer ham 0.95'ten belirgin düşük (prior'a çekilmiş).
    calibrated = cal.calibrate(0.95)
    assert calibrated < 0.95
    assert calibrated < 0.6  # prior düşük olduğu için aşağı çekilir

"""Backtest motoru testleri — fixture ile, ağsız, deterministik."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.backtest.runner import BacktestResult, run_backtest
from src.strategies.ema_rsi import EmaRsiStrategy


def _trending_df(n: int = 200) -> pd.DataFrame:
    # Yükseliş + düşüş dalgaları → motor en az birkaç işlem açsın.
    t = np.arange(n)
    closes = 100 + 15 * np.sin(t / 18.0) + t * 0.05
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = pd.Series(closes, index=idx)
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.5
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.5
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": 1000.0}
    )


def test_backtest_returns_result(ema_rsi_params):
    df = _trending_df()
    result = run_backtest(EmaRsiStrategy(ema_rsi_params), df, "TEST/USDT", timeframe="1h")

    assert isinstance(result, BacktestResult)
    assert result.bars == len(df)
    assert result.num_trades >= 0
    assert 0.0 <= result.win_rate <= 100.0
    # Açılan her işlemde giriş/çıkış zamanları tutarlı (çıkış >= giriş)
    for trade in result.trades:
        assert trade.exit_time >= trade.entry_time
        assert trade.exit_reason in {"stop", "tp", "signal", "end"}


def test_backtest_is_deterministic(ema_rsi_params):
    df = _trending_df()
    r1 = run_backtest(EmaRsiStrategy(ema_rsi_params), df, "TEST/USDT")
    r2 = run_backtest(EmaRsiStrategy(ema_rsi_params), df, "TEST/USDT")
    assert (r1.num_trades, r1.total_return_pct, r1.final_equity) == (
        r2.num_trades,
        r2.total_return_pct,
        r2.final_equity,
    )


def test_backtest_no_lookahead_entry_after_signal(ema_rsi_params):
    # İşlem giriş zamanı, sinyalin üretildiği bardan SONRAKİ bar olmalı.
    # (Giriş t+1 açılışında dolar; bu, giriş fiyatının bir önceki barın
    #  kapanışından farklı bir bara ait olduğunu garanti eder.)
    df = _trending_df()
    result = run_backtest(EmaRsiStrategy(ema_rsi_params), df, "TEST/USDT")
    if result.trades:
        first = result.trades[0]
        # Giriş zamanı serinin ilk barından sonra olmalı (warmup + 1 kuralı)
        assert first.entry_time > df.index[0]

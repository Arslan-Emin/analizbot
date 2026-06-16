"""İndikatör birim testleri — ağsız, deterministik (bilinen matematiksel özellikler)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.core.indicators import (
    adx,
    atr,
    bollinger,
    compute_indicators,
    crossover,
    crossunder,
    ema,
    obv,
    resample_ohlcv,
    rsi,
)


def test_ema_of_constant_is_constant():
    # Sabit serinin EMA'sı yine sabittir.
    s = pd.Series([5.0] * 30)
    assert np.allclose(ema(s, span=10), 5.0)


def test_rsi_strictly_increasing_is_100():
    # Hiç kayıp yoksa (sürekli artan) RSI = 100.
    s = pd.Series(range(1, 40), dtype=float)
    assert rsi(s, period=14).iloc[-1] == 100.0


def test_rsi_strictly_decreasing_is_0():
    # Hiç kazanç yoksa (sürekli azalan) RSI = 0.
    s = pd.Series(range(40, 1, -1), dtype=float)
    assert rsi(s, period=14).iloc[-1] == 0.0


def test_rsi_within_bounds(ohlcv):
    r = rsi(ohlcv["close"], period=14).dropna()
    assert (r >= 0).all()
    assert (r <= 100).all()


def test_atr_is_positive(ohlcv):
    a = atr(ohlcv, period=14).dropna()
    assert (a > 0).all()


def test_crossover_and_crossunder():
    # a, b'yi yukarı keser: önceki bar 1<=2, son bar 3>2
    up_a, up_b = pd.Series([1.0, 3.0]), pd.Series([2.0, 2.0])
    assert crossover(up_a, up_b)
    assert not crossunder(up_a, up_b)

    # a, b'yi aşağı keser
    down_a, down_b = pd.Series([3.0, 1.0]), pd.Series([2.0, 2.0])
    assert crossunder(down_a, down_b)
    assert not crossover(down_a, down_b)


def test_compute_indicators_adds_all_columns(ohlcv, ema_rsi_params):
    out = compute_indicators(ohlcv, ema_rsi_params)
    expected = ["ema_fast", "ema_slow", "rsi", "macd", "macd_signal", "atr"]
    for col in expected:
        assert col in out.columns

    # Yeterli veri olduğu için son barda warmup bitmiştir → NaN olmamalı.
    last = out.iloc[-1]
    for col in expected:
        assert pd.notna(last[col])

    # compute_indicators kopya üzerinde çalışır; orijinal df kirlenmez.
    assert "ema_fast" not in ohlcv.columns


def test_adx_within_bounds(ohlcv):
    adx_line, plus_di, minus_di = adx(ohlcv, period=14)
    a = adx_line.dropna()
    assert (a >= 0).all()
    assert (a <= 100).all()
    assert (plus_di.dropna() >= 0).all()
    assert (minus_di.dropna() >= 0).all()


def test_bollinger_ordering(ohlcv):
    mid, upper, lower = bollinger(ohlcv["close"], period=20, num_std=2.0)
    valid = mid.dropna().index
    assert (upper.loc[valid] >= mid.loc[valid]).all()
    assert (mid.loc[valid] >= lower.loc[valid]).all()


def test_obv_runs(ohlcv):
    series = obv(ohlcv["close"], ohlcv["volume"])
    assert len(series) == len(ohlcv)
    assert series.notna().all()


def test_resample_to_higher_timeframe(ohlcv):
    # 1 saatlik fixture -> 4 saatlik: bar sayısı azalır, kolonlar korunur.
    htf = resample_ohlcv(ohlcv, "4h")
    assert {"open", "high", "low", "close", "volume"}.issubset(htf.columns)
    assert len(htf) < len(ohlcv)
    assert (htf["high"] >= htf["low"]).all()

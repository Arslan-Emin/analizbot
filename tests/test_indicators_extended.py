"""Ek indikatörlerin birim testleri — ağsız, matematiksel sınır özellikleri."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.core.indicators import (
    bearish_engulfing,
    bullish_engulfing,
    cci,
    compute_indicators,
    doji,
    hammer,
    keltner,
    mfi,
    shooting_star,
    stoch_rsi,
    stochastic,
    supertrend,
    vwap,
    williams_r,
)


def test_stochastic_within_bounds(ohlcv):
    k, d = stochastic(ohlcv, k_period=14, d_period=3)
    for s in (k.dropna(), d.dropna()):
        assert (s >= 0).all()
        assert (s <= 100).all()


def test_stoch_rsi_within_bounds(ohlcv):
    k, d = stoch_rsi(ohlcv["close"])
    for s in (k.dropna(), d.dropna()):
        assert (s >= 0).all()
        assert (s <= 100).all()


def test_mfi_within_bounds(ohlcv):
    m = mfi(ohlcv, period=14).dropna()
    assert (m >= 0).all()
    assert (m <= 100).all()


def test_mfi_all_up_is_100():
    # Tipik fiyat sürekli artarsa negatif akış yok → MFI 100.
    n = 40
    close = pd.Series(np.arange(1, n + 1), dtype=float)
    df = pd.DataFrame(
        {"high": close + 0.5, "low": close - 0.5, "close": close, "volume": 10.0}
    )
    assert mfi(df, period=14).iloc[-1] == 100.0


def test_williams_r_within_bounds(ohlcv):
    wr = williams_r(ohlcv, period=14).dropna()
    assert (wr >= -100).all()
    assert (wr <= 0).all()


def test_cci_is_finite(ohlcv):
    c = cci(ohlcv, period=20)
    assert np.isfinite(c.to_numpy()).all()  # sıfıra bölme 0'a düşürülür


def test_supertrend_direction_is_signed(ohlcv):
    line, direction = supertrend(ohlcv, period=10, multiplier=3.0)
    assert len(line) == len(ohlcv)
    assert set(np.unique(direction)).issubset({-1.0, 1.0})
    # Yön +1 iken fiyat çizginin üstünde, -1 iken altında olmalı (tutarlılık).
    up = direction == 1.0
    assert (ohlcv["close"][up] >= line[up]).all()


def test_vwap_between_low_and_high_extremes(ohlcv):
    v = vwap(ohlcv, window=20).dropna()
    # VWAP, ilgili pencere içindeki fiyat aralığında kalmalı (kabaca makul sınır).
    assert (v > 0).all()
    assert (v <= ohlcv["high"].max()).all()
    assert (v >= ohlcv["low"].min()).all()


def test_keltner_ordering(ohlcv):
    mid, upper, lower = keltner(ohlcv, period=20, multiplier=2.0)
    valid = mid.dropna().index
    assert (upper.loc[valid] >= mid.loc[valid]).all()
    assert (mid.loc[valid] >= lower.loc[valid]).all()


def test_candlestick_patterns_return_bool(ohlcv):
    for fn in (doji, hammer, shooting_star, bullish_engulfing, bearish_engulfing):
        s = fn(ohlcv)
        assert s.dtype == bool
        assert len(s) == len(ohlcv)


def test_bullish_engulfing_detects_known_pattern():
    # 2 bar: önce ayı (10->9), sonra onu saran boğa (8.5->10.5).
    df = pd.DataFrame(
        {
            "open": [10.0, 8.5],
            "high": [10.2, 10.6],
            "low": [8.8, 8.4],
            "close": [9.0, 10.5],
            "volume": [1.0, 1.0],
        }
    )
    assert bool(bullish_engulfing(df).iloc[-1]) is True
    assert bool(bearish_engulfing(df).iloc[-1]) is False


def test_extended_indicators_opt_in(ohlcv, ema_rsi_params):
    # Varsayılan: ek kolonlar EKLENMEZ (geriye dönük uyum).
    base = compute_indicators(ohlcv, ema_rsi_params)
    assert "stoch_k" not in base.columns
    assert "supertrend_dir" not in base.columns

    # Bayrak açıkken: ek kolonlar eklenir.
    params = {**ema_rsi_params, "extended_indicators": True}
    ext = compute_indicators(ohlcv, params)
    for col in ("stoch_k", "stoch_d", "mfi", "cci", "williams_r", "supertrend", "supertrend_dir"):
        assert col in ext.columns

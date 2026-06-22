"""Pair trading / cointegration testleri — seed'li sentetik veri, deterministik."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.strategies.pairs import _half_life, _ols_hedge, analyze_pair


def test_ols_hedge_recovers_beta():
    b = np.linspace(1, 100, 200)
    a = 2.5 * b + 3.0  # gürültüsüz → tam geri kazanım
    beta, alpha = _ols_hedge(a, b)
    assert abs(beta - 2.5) < 1e-6
    assert abs(alpha - 3.0) < 1e-6


def test_half_life_mean_reverting():
    rng = np.random.default_rng(0)
    s = np.zeros(300)
    for i in range(1, 300):
        s[i] = 0.7 * s[i - 1] + rng.normal(0, 1)  # AR(1), λ=-0.3 → HL≈1.9
    hl = _half_life(s)
    assert hl is not None
    assert 1.0 < hl < 4.0


def test_half_life_random_walk_not_mean_reverting():
    rng = np.random.default_rng(1)
    rw = np.cumsum(rng.normal(0, 1, 300))  # random walk → dönmüyor
    hl = _half_life(rw)
    assert hl is None or hl > 20  # ya dönmez ya da çok yavaş


def _cointegrated_pair(seed: int = 42, n: int = 300, beta: float = 2.0):
    rng = np.random.default_rng(seed)
    b = 100 + np.cumsum(rng.normal(0, 1, n))      # random walk
    noise = rng.normal(0, 0.5, n)                 # durağan (stationary)
    a = beta * b + 5.0 + noise                    # a, b ile cointegre
    return pd.Series(a), pd.Series(b)


def test_analyze_pair_detects_cointegration():
    a, b = _cointegrated_pair()
    res = analyze_pair(a, b, "A/USDT", "B/USDT", {"coint_pvalue": 0.05})
    assert res.cointegrated is True
    assert res.coint_pvalue < 0.05
    assert abs(res.hedge_ratio - 2.0) < 0.1


def test_analyze_pair_short_spread_on_high_z():
    a, b = _cointegrated_pair()
    a.iloc[-1] += 6.0  # son spread aşırı pozitif → z yüksek → A pahalı → SHORT_SPREAD
    res = analyze_pair(a, b, "A/USDT", "B/USDT", {"z_entry": 2.0})
    assert res.zscore > 2.0
    assert res.signal == "SHORT_SPREAD"


def test_analyze_pair_long_spread_on_low_z():
    a, b = _cointegrated_pair()
    a.iloc[-1] -= 6.0  # son spread aşırı negatif → A ucuz → LONG_SPREAD
    res = analyze_pair(a, b, "A/USDT", "B/USDT", {"z_entry": 2.0})
    assert res.zscore < -2.0
    assert res.signal == "LONG_SPREAD"


def test_analyze_pair_insufficient_data():
    a = pd.Series(np.arange(20.0))
    b = pd.Series(np.arange(20.0))
    res = analyze_pair(a, b, "A", "B", {})
    assert res.signal == "FLAT"
    assert not res.cointegrated

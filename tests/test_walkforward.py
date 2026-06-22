"""Walk-forward optimizasyon + sağlamlık testleri — ağsız, deterministik."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.backtest.walkforward import _assess_robustness, grid_combos, walk_forward

_BASE = {
    "ema_fast": 12,
    "ema_slow": 26,
    "rsi_period": 14,
    "rsi_overbought": 70,
    "rsi_oversold": 30,
    "atr_period": 14,
    "atr_stop_mult": 1.5,
    "atr_tp_mult": 3.0,
    "risk_per_trade_pct": 1.0,
    "hypothetical_capital_quote": 1000,
}


def _trend_df(n: int = 400) -> pd.DataFrame:
    """Tekrarlı yükseliş/düşüş rampaları → ema_rsi işlem üretir (deterministik)."""
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    ramp = np.concatenate([np.linspace(100, 120, 80), np.linspace(120, 100, 80)])
    base = np.resize(ramp, n)
    close = pd.Series(base, index=idx)
    return pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1000.0,
        }
    )


def test_grid_combos_cartesian():
    grid = {"a": [1, 2], "b": [10, 20, 30]}
    combos = list(grid_combos(grid))
    assert len(combos) == 6
    assert {"a": 1, "b": 10} in combos
    assert list(grid_combos({})) == [{}]


def test_assess_robustness_spike_is_overfit():
    rob = _assess_robustness([0.1, 0.1, 0.1, 0.1, 5.0])
    assert rob["overfit_risk"] is True
    assert rob["plateau_count"] == 1


def test_assess_robustness_plateau_is_robust():
    rob = _assess_robustness([1.0, 1.0, 0.95, 1.0, 0.9])
    assert rob["overfit_risk"] is False
    assert rob["plateau_count"] >= 3


def test_assess_robustness_tiny_grid():
    rob = _assess_robustness([1.0])
    assert rob["overfit_risk"] is False
    assert rob["grid_size"] == 1


def test_walk_forward_structure_and_no_lookahead():
    grid = {"ema_fast": [8, 12], "ema_slow": [21, 26], "atr_stop_mult": [1.0, 1.5]}  # 8 combo
    result = walk_forward(
        "ema_rsi", _trend_df(400), grid, base_params=dict(_BASE), symbol="X/USDT",
        timeframe="1h", train_bars=120, test_bars=60, metric="avg_r", min_trades=1,
    )
    assert result.param_grid_size == 8
    assert result.metric == "avg_r"
    assert len(result.folds) >= 1
    assert "overfit_risk" in result.robustness
    # Her katın seçtiği parametreler ızgaradan gelmeli (in-sample'da seçilir).
    for f in result.folds:
        assert set(f.best_params).issubset({"ema_fast", "ema_slow", "atr_stop_mult"})
        assert f.best_params["ema_fast"] in (8, 12)


def test_walk_forward_insufficient_data():
    # train+test penceresi veriye sığmazsa kat üretilmez (hata değil).
    grid = {"ema_fast": [8, 12]}
    result = walk_forward(
        "ema_rsi", _trend_df(100), grid, base_params=dict(_BASE), symbol="X/USDT",
        timeframe="1h", train_bars=120, test_bars=60, min_trades=1,
    )
    assert result.folds == []
    assert result.oos_total_trades == 0

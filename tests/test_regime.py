"""Piyasa rejimi + kapılama testleri — ağsız, deterministik, look-ahead kontrollü."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.core.models import Action, Signal
from src.core.regime import (
    RegimeState,
    assess_trend_regime,
    compute_breadth,
    gate_signal,
    make_backtest_regime_fn,
)
from src.strategies.base import Strategy
from src.strategies.regime_filtered import RegimeFilteredStrategy

_CFG: dict = {
    "trend_period": 200,
    "trend_dist_sat_pct": 5.0,
    "adx_period": 14,
    "adx_min": 20,
    "risk_on_score": 0.34,
    "risk_off_score": -0.34,
    "risk_on_ceiling": 1.0,
    "neutral_ceiling": 0.6,
    "risk_off_ceiling": 0.25,
    "mode": "soft",
    "penalty": 0.5,
}


def _df(closes: np.ndarray, freq: str = "D") -> pd.DataFrame:
    idx = pd.date_range("2023-01-01", periods=len(closes), freq=freq, tz="UTC")
    close = pd.Series(closes, index=idx, dtype=float)
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.5
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.5
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": 1000.0}
    )


# --------------------------- assess_trend_regime --------------------------- #


def test_strong_uptrend_is_risk_on():
    a = assess_trend_regime(_df(np.linspace(100, 200, 260)), _CFG)
    assert a.state == RegimeState.RISK_ON
    assert a.score > 0.34
    assert a.position_bias == "long"
    assert a.ready


def test_strong_downtrend_is_risk_off():
    a = assess_trend_regime(_df(np.linspace(200, 100, 260)), _CFG)
    assert a.state == RegimeState.RISK_OFF
    assert a.score < -0.34
    assert a.position_bias == "short"


def test_flat_market_is_neutral():
    # EMA çevresinde küçük salınım → mesafe ~0, eğim ~0, ADX zayıf → NEUTRAL.
    closes = 100 + 0.2 * np.sin(np.linspace(0, 20 * np.pi, 260))
    a = assess_trend_regime(_df(closes), _CFG)
    assert a.state == RegimeState.NEUTRAL


def test_insufficient_data_not_ready():
    a = assess_trend_regime(_df(np.linspace(100, 110, 50)), _CFG)
    assert a.state == RegimeState.NEUTRAL
    assert not a.ready  # 50 bar < 200+5 → kapılama devre dışı


# ------------------------------- gate_signal ------------------------------- #


def _risk_off():
    return assess_trend_regime(_df(np.linspace(200, 100, 260)), _CFG)


def _risk_on():
    return assess_trend_regime(_df(np.linspace(100, 200, 260)), _CFG)


def test_soft_penalizes_counter_regime_buy():
    action, conf, reason = gate_signal(Action.BUY, 0.8, _risk_off(), _CFG)
    assert action == Action.BUY
    assert conf == 0.4  # 0.8 * 0.5
    assert reason is not None and "RISK_OFF" in reason


def test_soft_leaves_pro_regime_untouched():
    action, conf, reason = gate_signal(Action.SELL, 0.8, _risk_off(), _CFG)
    assert (action, conf, reason) == (Action.SELL, 0.8, None)


def test_gate_mode_converts_counter_to_hold():
    cfg = {**_CFG, "mode": "gate"}
    action, _conf, reason = gate_signal(Action.BUY, 0.8, _risk_off(), cfg)
    assert action == Action.HOLD
    assert reason is not None


def test_neutral_regime_no_change():
    closes = 100 + 0.2 * np.sin(np.linspace(0, 20 * np.pi, 260))
    neutral = assess_trend_regime(_df(closes), _CFG)
    assert gate_signal(Action.BUY, 0.7, neutral, _CFG) == (Action.BUY, 0.7, None)


def test_hold_never_gated():
    assert gate_signal(Action.HOLD, 0.5, _risk_off(), _CFG) == (Action.HOLD, 0.5, None)


# ------------------------- RegimeFilteredStrategy -------------------------- #


class _FakeStrategy(Strategy):
    name = "fake"

    def __init__(self, action: Action) -> None:
        self.params = {}
        self._action = action

    def generate(self, df: pd.DataFrame, symbol: str) -> Signal:
        return Signal(
            symbol=symbol, action=self._action, confidence=0.8, price=100.0,
            reasons=["taban"], suggested_entry=100.0, stop_loss=95.0,
            take_profit=110.0, suggested_size_quote=10.0,
        )


def test_wrapper_soft_reduces_confidence_and_keeps_name():
    off = _risk_off()
    wrapped = RegimeFilteredStrategy(_FakeStrategy(Action.BUY), lambda _df: off, _CFG)
    sig = wrapped.generate(_df(np.linspace(100, 101, 5)), "X/USDT")
    assert sig.action == Action.BUY
    assert sig.confidence == 0.4
    assert any("RISK_OFF" in r for r in sig.reasons)
    assert wrapped.name == "fake"  # depolama/kalibrasyon sürekliliği


def test_wrapper_gate_clears_levels():
    off = _risk_off()
    cfg = {**_CFG, "mode": "gate"}
    wrapped = RegimeFilteredStrategy(_FakeStrategy(Action.BUY), lambda _df: off, cfg)
    sig = wrapped.generate(_df(np.linspace(100, 101, 5)), "X/USDT")
    assert sig.action == Action.HOLD
    assert sig.stop_loss is None and sig.take_profit is None


def test_wrapper_none_assessment_passthrough():
    wrapped = RegimeFilteredStrategy(_FakeStrategy(Action.BUY), lambda _df: None, _CFG)
    sig = wrapped.generate(_df(np.linspace(100, 101, 5)), "X/USDT")
    assert sig.action == Action.BUY and sig.confidence == 0.8


# ------------------------------- breadth ----------------------------------- #


class _FakeProvider:
    """fetch_ohlcv: ilk yarısı MA üstünde (yükselen), ikinci yarısı altında (düşen)."""

    def __init__(self, n_up: int, n_down: int) -> None:
        self._up = [f"UP{i}/USDT" for i in range(n_up)]
        self._down = [f"DN{i}/USDT" for i in range(n_down)]

    def all_symbols(self) -> list[str]:
        return self._up + self._down

    def fetch_ohlcv(self, symbol, timeframe="1d", limit=120):
        rising = symbol.startswith("UP")
        closes = np.linspace(10, 20, 80) if rising else np.linspace(20, 10, 80)
        return _df(closes)


def test_compute_breadth_percentage():
    prov = _FakeProvider(n_up=7, n_down=3)
    pct = compute_breadth(prov, prov.all_symbols(), ma_period=50)
    assert pct == 70.0  # 7/10 yükselen → %70 MA üstünde


# ------------------------- backtest regime fn (look-ahead) ----------------- #


def test_backtest_regime_fn_no_lookahead():
    # 260 günlük yükselen benchmark; her gün için rejim önceden hesaplanır.
    bench = _df(np.linspace(100, 200, 260))
    fn = make_backtest_regime_fn(bench, _CFG)

    # İlk barda biten pencere: eşik (ts-1gün) ilk bardan önce → None.
    assert fn(bench.iloc[:1]) is None

    # Erken pencere: eşleşen günlük bar var ama geçmişi yetersiz → ready=False
    # (gate bunu no-op sayar, yani kapılama yapılmaz).
    early = fn(bench.iloc[:3])
    assert early is not None and not early.ready

    # Geç pencere: yeterli geçmiş → hazır, geçerli bir durum.
    a = fn(bench.iloc[:250])
    assert a is not None and a.ready
    assert a.state in (RegimeState.RISK_ON, RegimeState.NEUTRAL, RegimeState.RISK_OFF)

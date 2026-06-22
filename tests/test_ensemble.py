"""EnsembleStrategy testleri — ağırlıklı oylama, uzlaşı eşiği, dinamik ağırlık."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.core.models import Action, Signal
from src.strategies.base import Strategy
from src.strategies.ensemble import EnsembleStrategy, dynamic_weights_from_stats
from src.strategies.registry import build_strategy

_PARAMS = {
    "atr_period": 14,
    "atr_stop_mult": 1.5,
    "atr_tp_mult": 3.0,
    "risk_per_trade_pct": 1.0,
    "hypothetical_capital_quote": 1000,
    "min_agreement": 2,
    "timeframe": "1h",
}


def _df(n: int = 40) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = pd.Series(np.linspace(100, 105, n), index=idx)
    return pd.DataFrame(
        {"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1000.0}
    )


class _Member(Strategy):
    """Sabit bir aksiyon/güven döndüren sahte üye (oylama mantığını izole test eder)."""

    def __init__(self, action: Action, conf: float = 0.7) -> None:
        self.params = {}
        self._a = action
        self._c = conf

    def generate(self, df: pd.DataFrame, symbol: str) -> Signal:
        return Signal(symbol=symbol, action=self._a, confidence=self._c, price=100.0, reasons=["m"])


def _ensemble(members: list[tuple[str, float, Strategy]], **overrides) -> EnsembleStrategy:
    e = EnsembleStrategy({**_PARAMS, **overrides})
    e.members = members
    return e


def test_unanimous_buy():
    e = _ensemble([
        ("a", 1.0, _Member(Action.BUY, 0.6)),
        ("b", 1.0, _Member(Action.BUY, 0.8)),
        ("c", 1.0, _Member(Action.BUY, 0.7)),
    ])
    sig = e.generate(_df(), "X/USDT")
    assert sig.action == Action.BUY
    assert sig.stop_loss < sig.suggested_entry < sig.take_profit
    assert 0.6 <= sig.confidence <= 0.8  # ağırlıklı ortalama güven aralığı


def test_majority_buy_meets_min_agreement():
    e = _ensemble([
        ("a", 1.0, _Member(Action.BUY, 0.7)),
        ("b", 1.0, _Member(Action.BUY, 0.7)),
        ("c", 1.0, _Member(Action.SELL, 0.7)),
    ])
    assert e.generate(_df(), "X/USDT").action == Action.BUY


def test_no_consensus_holds():
    # 1 BUY, 1 SELL, 1 HOLD → hiçbir yön min_agreement=2'ye ulaşmaz → HOLD.
    e = _ensemble([
        ("a", 1.0, _Member(Action.BUY, 0.7)),
        ("b", 1.0, _Member(Action.SELL, 0.7)),
        ("c", 1.0, _Member(Action.HOLD, 0.5)),
    ])
    sig = e.generate(_df(), "X/USDT")
    assert sig.action == Action.HOLD
    assert sig.stop_loss is None


def test_weight_and_agreement_resolve_direction():
    # 2 BUY (ağırlık 1, güven 0.5 → buy_w=1.0) vs 1 SELL (ağırlık 1, güven 0.9 → sell_w=0.9).
    # buy_n=2 ≥ min_agreement ve buy_w > sell_w → BUY.
    e = _ensemble([
        ("a", 1.0, _Member(Action.BUY, 0.5)),
        ("b", 1.0, _Member(Action.BUY, 0.5)),
        ("c", 1.0, _Member(Action.SELL, 0.9)),
    ])
    assert e.generate(_df(), "X/USDT").action == Action.BUY


def test_min_agreement_three_blocks_two_votes():
    # min_agreement=3 iken 2 BUY yeterli değil → HOLD.
    e = _ensemble(
        [
            ("a", 1.0, _Member(Action.BUY, 0.8)),
            ("b", 1.0, _Member(Action.BUY, 0.8)),
            ("c", 1.0, _Member(Action.HOLD, 0.5)),
        ],
        min_agreement=3,
    )
    assert e.generate(_df(), "X/USDT").action == Action.HOLD


def test_member_exception_isolated():
    class _Boom(Strategy):
        def __init__(self):
            self.params = {}

        def generate(self, df, symbol):
            raise RuntimeError("patladı")

    e = _ensemble([
        ("a", 1.0, _Member(Action.BUY, 0.7)),
        ("b", 1.0, _Member(Action.BUY, 0.7)),
        ("boom", 1.0, _Boom()),
    ])
    # Patlayan üye atlanır; kalan 2 BUY ile karar verilir.
    assert e.generate(_df(), "X/USDT").action == Action.BUY


# ------------------------------ dinamik ağırlık ---------------------------- #


class _FakeRepo:
    def outcomes(self, strategy=None, symbol=None):
        if strategy == "ema_rsi":
            return [
                {"realized_return_pct": 2.0, "confidence": 0.6, "r_multiple": 1.0},
                {"realized_return_pct": -1.0, "confidence": 0.5, "r_multiple": -1.0},
            ]
        return []


def test_dynamic_weights_from_stats():
    w = dynamic_weights_from_stats(_FakeRepo(), ["ema_rsi", "ml"])
    assert w["ema_rsi"] == 0.5  # 1 kazanan / 2 = %50 hit_rate → 0.5
    assert w["ml"] == 1.0  # geçmiş yok → nötr 1.0


# ------------------------------ registry entegrasyonu ---------------------- #


def test_registry_builds_ensemble():
    strat = build_strategy("ensemble", {**_PARAMS, "members": [{"name": "ema_rsi", "weight": 1.0}]})
    assert isinstance(strat, EnsembleStrategy)
    sig = strat.generate(_df(60), "X/USDT")
    assert sig.action in (Action.BUY, Action.SELL, Action.HOLD)
    assert strat.name == "ensemble"

"""Strateji adı → sınıf eşlemesi. Yeni strateji eklemek = buraya bir satır.

Bu, CLI/scheduler/backtest'in config'teki `active_strategy` adından doğru
strateji nesnesini kurmasını sağlar.
"""

from __future__ import annotations

from src.strategies.base import Strategy
from src.strategies.confluence import ConfluenceStrategy
from src.strategies.ema_rsi import EmaRsiStrategy
from src.strategies.ml_strategy import MlStrategy

_STRATEGIES: dict[str, type[Strategy]] = {
    "ema_rsi": EmaRsiStrategy,
    "confluence": ConfluenceStrategy,
    "ml": MlStrategy,
}


def build_strategy(name: str, params: dict) -> Strategy:
    try:
        strategy_cls = _STRATEGIES[name]
    except KeyError as exc:
        raise ValueError(
            f"Bilinmeyen strateji: {name!r}. Mevcut: {list(_STRATEGIES)}"
        ) from exc
    return strategy_cls(params)

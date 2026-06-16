"""Paylaşılan seviye hesabı: ATR'ye dayalı giriş/stop/hedef + riskten boyut.

Hem EmaRsiStrategy hem ConfluenceStrategy bunu kullanır (tek kaynak).
NOT: Üretilen seviyeler ÖRNEK/EĞİTSEL'dir; emir değildir.
"""

from __future__ import annotations

from src.core.models import Action


def compute_levels(
    action: Action, price: float, atr_val: float, params: dict
) -> tuple[float, float | None, float | None, float | None]:
    stop_mult = float(params.get("atr_stop_mult", 1.5))
    tp_mult = float(params.get("atr_tp_mult", 3.0))
    risk_pct = float(params.get("risk_per_trade_pct", 1.0))
    capital = float(params.get("hypothetical_capital_quote", 1000))

    entry = price
    if action == Action.BUY:
        stop = entry - stop_mult * atr_val
        take_profit = entry + tp_mult * atr_val
    else:  # SELL
        stop = entry + stop_mult * atr_val
        take_profit = entry - tp_mult * atr_val

    # Riskten boyutlama: sermayenin risk%'i / birim risk * fiyat.
    risk_per_unit = abs(entry - stop)
    size = (capital * risk_pct / 100.0) / risk_per_unit * entry if risk_per_unit > 0 else None

    return (
        round(entry, 2),
        round(stop, 2),
        round(take_profit, 2),
        round(size, 2) if size is not None else None,
    )

"""Pozisyon boyutlama testleri — saf matematik, deterministik."""

from __future__ import annotations

from src.core.models import Action
from src.strategies.levels import compute_levels
from src.strategies.sizing import compute_size, kelly_fraction


def test_fixed_fractional():
    # risk %1, sermaye 1000, stop mesafesi 2 → (1000*0.01)/2 * 100 = 500 notional.
    size = compute_size(
        "fixed_fractional", capital=1000, entry=100, stop=98, atr_val=2,
        params={"risk_per_trade_pct": 1.0},
    )
    assert size == 500.0


def test_atr_target_vol():
    # vol%=atr/entry=0.02, hedef %1 → f=0.5 → 1000*0.5 = 500.
    size = compute_size(
        "atr_target_vol", capital=1000, entry=100, stop=98, atr_val=2,
        params={"target_vol_pct": 1.0},
    )
    assert size == 500.0


def test_kelly_with_inputs():
    # W=0.6, R=2 → f = 0.6 - 0.4/2 = 0.4; yarım Kelly → 0.2; cap 0.25 → 0.2 → 200.
    size = compute_size(
        "kelly", capital=1000, entry=100, stop=98, atr_val=2,
        params={"kelly_win_rate": 0.6, "kelly_payoff": 2.0},
    )
    assert size == 200.0


def test_kelly_falls_back_without_inputs():
    # Kelly girdisi yok → fixed_fractional ile aynı (500).
    size = compute_size(
        "kelly", capital=1000, entry=100, stop=98, atr_val=2,
        params={"risk_per_trade_pct": 1.0},
    )
    assert size == 500.0


def test_max_position_pct_cap():
    # Çok sıkı stop → büyük boyut; max_position_pct=20 → 1000*0.2 = 200 ile sınırlanır.
    size = compute_size(
        "fixed_fractional", capital=1000, entry=100, stop=99.9, atr_val=0.1,
        params={"risk_per_trade_pct": 1.0, "max_position_pct": 20},
    )
    assert size == 200.0


def test_kelly_fraction_no_edge_is_zero():
    assert kelly_fraction(0.4, 1.0) == 0.0  # f = 0.4 - 0.6/1 < 0 → 0
    assert kelly_fraction(0.6, 0.0) == 0.0  # payoff 0 → 0


def test_kelly_fraction_full_vs_half():
    full = kelly_fraction(0.6, 2.0, half=False, cap=1.0)
    half = kelly_fraction(0.6, 2.0, half=True, cap=1.0)
    assert abs(full - 0.4) < 1e-9
    assert abs(half - 0.2) < 1e-9


def test_compute_levels_uses_sizing_method():
    # compute_levels, sizing_method'u params'tan okur (atr_target_vol).
    entry, stop, tp, size = compute_levels(
        Action.BUY, 100.0, 2.0,
        {"atr_stop_mult": 1.5, "atr_tp_mult": 3.0, "hypothetical_capital_quote": 1000,
         "sizing_method": "atr_target_vol", "target_vol_pct": 1.0},
    )
    assert stop < entry < tp
    assert size == 500.0  # 1000 * (0.01 / (2/100))


def test_compute_levels_default_unchanged():
    # sizing_method verilmezse fixed_fractional (eski davranış birebir).
    _, _, _, size = compute_levels(
        Action.BUY, 100.0, 2.0,
        {"atr_stop_mult": 1.0, "atr_tp_mult": 3.0, "risk_per_trade_pct": 1.0,
         "hypothetical_capital_quote": 1000},
    )
    # stop = 100 - 1*2 = 98 → risk_per_unit 2 → (1000*0.01)/2*100 = 500.
    assert size == 500.0

"""Performans koçu testleri — saf fonksiyon, ağsız."""

from __future__ import annotations

from src.learning.coach import coach_review


def _rows(returns, confs=None, rmults=None):
    confs = confs or [0.6] * len(returns)
    rmults = rmults if rmults is not None else [r / 2.0 for r in returns]
    return [
        {
            "realized_return_pct": r, "confidence": c, "r_multiple": rm,
            "outcome": "WIN" if r > 0 else "LOSS", "action": "BUY",
        }
        for r, c, rm in zip(returns, confs, rmults, strict=True)
    ]


def _axes(review):
    return {name: level for name, level, _ in review["axes"]}


def test_coach_empty():
    rev = coach_review([])
    assert rev["n"] == 0
    assert rev["axes"][0][1] == "REVIEW"


def test_coach_positive_expectancy_ok():
    # 15 kazanan (+3, R=1) + 10 kaybeden (-1, R=-0.5) → avg_R=0.4, isabet %60, payoff 3.0.
    rows = _rows([3.0] * 15 + [-1.0] * 10, rmults=[1.0] * 15 + [-0.5] * 10)
    rev = coach_review(rows)
    assert rev["n"] == 25
    axes = _axes(rev)
    assert axes["Beklenti (avg R)"] == "OK"
    assert axes["Risk disiplini (payoff)"] == "OK"
    assert axes["Tutarlılık (isabet)"] == "OK"
    assert axes["Örneklem"] == "OK"


def test_coach_negative_expectancy_review():
    rows = _rows([-2.0] * 15 + [1.0] * 5, rmults=[-1.0] * 15 + [0.5] * 5)
    rev = coach_review(rows)
    axes = _axes(rev)
    assert axes["Beklenti (avg R)"] == "REVIEW"  # avg_R negatif
    assert axes["Tutarlılık (isabet)"] == "REVIEW"  # isabet %25 < 35


def test_coach_small_sample_review():
    rev = coach_review(_rows([1.0, 2.0, -1.0]))
    assert _axes(rev)["Örneklem"] == "REVIEW"  # n=3 < 10


def test_coach_metrics_computed():
    rows = _rows([2.0, -1.0, 2.0, -1.0], confs=[0.5, 0.5, 0.5, 0.5], rmults=[1, -1, 1, -1])
    rev = coach_review(rows)
    assert rev["win_rate"] == 50.0
    assert rev["payoff"] == 2.0  # avg_win 2 / |avg_loss 1|

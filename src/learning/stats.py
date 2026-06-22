"""Başarı istatistikleri — çözülmüş sinyallerden performans özeti.

İsabet oranı, ortalama R, beklenen değer (expectancy = ortalama getiri) ve
**Brier skoru** (güven skorunun gerçekle ne kadar kalibre olduğu; düşük = iyi).
"""

from __future__ import annotations

import pandas as pd

from src.storage.db import Repository

_GROUP_DEFAULT = ("strategy", "symbol", "action")


def performance(
    repo: Repository,
    *,
    group_by: tuple[str, ...] = _GROUP_DEFAULT,
    strategy: str | None = None,
    symbol: str | None = None,
) -> pd.DataFrame:
    """Çözülmüş sinyalleri gruplayıp performans tablosu döndürür.

    Kolonlar: n, hit_rate (%), avg_return_pct, avg_r (expectancy), brier.
    Gerçekleşen getiri > 0 ise "isabet" sayılır (stop/hedef/expired fark etmez).
    """
    rows = repo.outcomes(strategy=strategy, symbol=symbol)
    cols = ["n", "hit_rate", "avg_return_pct", "avg_r", "brier"]
    if not rows:
        return pd.DataFrame(columns=[*group_by, *cols])

    df = pd.DataFrame(rows)
    df["win"] = (df["realized_return_pct"] > 0).astype(int)
    df["sq_err"] = (df["confidence"] - df["win"]) ** 2  # Brier bileşeni

    out = (
        df.groupby(list(group_by))
        .agg(
            n=("win", "size"),
            hit_rate=("win", "mean"),
            avg_return_pct=("realized_return_pct", "mean"),
            avg_r=("r_multiple", "mean"),
            brier=("sq_err", "mean"),
        )
        .reset_index()
    )
    out["hit_rate"] = (out["hit_rate"] * 100.0).round(2)
    out["avg_return_pct"] = out["avg_return_pct"].round(3)
    out["avg_r"] = out["avg_r"].round(3)
    out["brier"] = out["brier"].round(4)
    return out.sort_values("n", ascending=False).reset_index(drop=True)


def kelly_inputs(
    repo: Repository, strategy: str | None = None, symbol: str | None = None
) -> tuple[float | None, float | None]:
    """Geçmiş çözülmüş sonuçlardan Kelly girdileri: (win_rate 0..1, payoff=avg_win/avg_loss).

    Hem kazanan hem kaybeden örnek yoksa (None, None) → çağıran fixed_fractional'a düşer.
    """
    rows = repo.outcomes(strategy=strategy, symbol=symbol)
    if not rows:
        return None, None
    df = pd.DataFrame(rows)
    returns = df["realized_return_pct"]
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    if len(df) == 0 or len(wins) == 0 or len(losses) == 0:
        return None, None
    avg_loss = abs(float(losses.mean()))
    if avg_loss <= 0:
        return None, None
    win_rate = len(wins) / len(df)
    payoff = float(wins.mean()) / avg_loss
    return round(float(win_rate), 4), round(float(payoff), 4)


def overall(repo: Repository, strategy: str | None = None) -> dict:
    """Tek satırlık genel özet (toplam sinyal, isabet, ort. getiri, Brier)."""
    rows = repo.outcomes(strategy=strategy)
    if not rows:
        return {"n": 0, "hit_rate": 0.0, "avg_return_pct": 0.0, "brier": 0.0}
    df = pd.DataFrame(rows)
    win = (df["realized_return_pct"] > 0).astype(int)
    return {
        "n": int(len(df)),
        "hit_rate": round(float(win.mean()) * 100.0, 2),
        "avg_return_pct": round(float(df["realized_return_pct"].mean()), 3),
        "brier": round(float(((df["confidence"] - win) ** 2).mean()), 4),
    }

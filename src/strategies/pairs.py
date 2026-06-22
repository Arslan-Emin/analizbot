"""Pair trading / cointegration — istatistiksel arbitraj (statistical arbitrage).

İki KORELE varlık (örn BTC ve ETH) uzun vadede birlikte hareket eder; aralarındaki
'spread' (fark) ortalamaya döner (mean reversion). Cointegration testi bu ilişkinin
istatistiksel olarak anlamlı olduğunu doğrular. Spread aşırı açıldığında ucuz olanı
AL, pahalı olanı SAT → spread kapanınca kâr (yöne değil, FARKA bahis).

İlham: tradermonty/claude-trading-skills — pair-trade-screener.

NOT: Tek-sembollü Strategy ABC'sine OTURMAZ (iki seri ister) → ayrı akış/komut olarak
modellenir. Üretilen sinyaller ÖRNEK/EĞİTSEL'dir; emir değildir. statsmodels gerektirir.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PairResult:
    symbol_a: str
    symbol_b: str
    cointegrated: bool
    coint_pvalue: float
    hedge_ratio: float          # a ≈ hedge_ratio * b + sabit
    zscore: float               # güncel spread z-skoru
    half_life: float | None     # ortalamaya dönüş yarı-ömrü (bar)
    signal: str                 # LONG_SPREAD | SHORT_SPREAD | FLAT
    n: int
    reasons: list[str] = field(default_factory=list)


def _ols_hedge(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """a = beta*b + alpha (en küçük kareler) → (beta, alpha)."""
    x = np.column_stack([b, np.ones(len(b))])
    beta, alpha = np.linalg.lstsq(x, a, rcond=None)[0]
    return float(beta), float(alpha)


def _half_life(spread: np.ndarray) -> float | None:
    """Ortalamaya dönüş yarı-ömrü (bar): Δs_t = λ·s_{t-1} + c → HL = -ln2 / ln(1+λ).

    λ ≥ 0 ise ortalamaya dönmüyor (None). Spread ne kadar hızlı kapanıyorsa HL o kadar küçük.
    """
    if len(spread) < 12:
        return None
    s_prev = spread[:-1]
    ds = np.diff(spread)
    x = np.column_stack([s_prev, np.ones(len(s_prev))])
    lam = np.linalg.lstsq(x, ds, rcond=None)[0][0]
    if lam >= 0:
        return None
    hl = -np.log(2) / np.log(1.0 + lam)
    return float(hl) if hl > 0 else None


def analyze_pair(
    close_a: pd.Series, close_b: pd.Series, symbol_a: str, symbol_b: str, params: dict
) -> PairResult:
    """İki fiyat serisinde cointegration + spread z-skoru → pair trade değerlendirmesi."""
    try:
        from statsmodels.tsa.stattools import coint
    except ImportError as exc:
        raise ValueError("statsmodels kurulu değil: pip install statsmodels") from exc

    df = pd.concat([close_a.rename("a"), close_b.rename("b")], axis=1).dropna()
    if len(df) < 30:
        return PairResult(
            symbol_a, symbol_b, False, 1.0, 0.0, 0.0, None, "FLAT", len(df),
            ["Yetersiz örtüşen veri (< 30 bar)"],
        )

    a = df["a"].to_numpy()
    b = df["b"].to_numpy()
    n = len(df)
    z_entry = float(params.get("z_entry", 2.0))
    z_exit = float(params.get("z_exit", 0.5))
    pmax = float(params.get("coint_pvalue", 0.05))
    window = int(params.get("zscore_window", 0)) or n

    pvalue = float(coint(a, b)[1])
    beta, alpha = _ols_hedge(a, b)
    spread = a - (beta * b + alpha)

    sp = spread[-window:]
    mu = float(sp.mean())
    sd = float(sp.std(ddof=0)) or 1e-9
    z = (float(spread[-1]) - mu) / sd
    hl = _half_life(spread)
    cointegrated = pvalue < pmax

    reasons = [
        f"Cointegration p={pvalue:.4f} "
        f"({'anlamlı' if cointegrated else 'zayıf'}, eşik {pmax})",
        f"Hedge oranı β={beta:.4f}; spread z={z:+.2f}",
    ]
    if hl is not None:
        reasons.append(f"Ortalamaya dönüş yarı-ömrü ~{hl:.0f} bar")

    signal = "FLAT"
    if not cointegrated:
        reasons.append("Cointegration zayıf → pair trade ÖNERİLMEZ.")
    elif z >= z_entry:
        signal = "SHORT_SPREAD"
        reasons.append(f"z ≥ {z_entry}: {symbol_a} SAT / {symbol_b} AL (spread kapanmasını bekle)")
    elif z <= -z_entry:
        signal = "LONG_SPREAD"
        reasons.append(f"z ≤ -{z_entry}: {symbol_a} AL / {symbol_b} SAT (spread kapanmasını bekle)")
    elif abs(z) <= z_exit:
        reasons.append(f"|z| ≤ {z_exit}: spread ortalamada (açık pozisyon varsa çıkış).")
    else:
        reasons.append("z giriş eşiğinde değil → bekle.")

    return PairResult(
        symbol_a=symbol_a,
        symbol_b=symbol_b,
        cointegrated=cointegrated,
        coint_pvalue=round(pvalue, 4),
        hedge_ratio=round(beta, 4),
        zscore=round(z, 2),
        half_life=round(hl, 1) if hl is not None else None,
        signal=signal,
        n=n,
        reasons=reasons,
    )

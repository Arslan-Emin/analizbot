"""Pozisyon çıkış simülasyonu — backtest ve geri besleme değerlendiricisi paylaşır.

Tek kaynak (single source of truth): bir barın yüksek/düşük değerine göre long/short
bir pozisyon stop'a mı yoksa hedefe mi değdi? **Kötümser varsayım:** stop ve hedef
AYNI bar içindeyse ÖNCE STOP dolmuş kabul edilir. Bu, `backtest/runner.py` ile birebir
aynı semantiktir → backtest / canlı / geçmiş değerlendirme paritesi korunur.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


def check_bar_exit(
    side: str, high: float, low: float, stop: float, tp: float
) -> tuple[float | None, str]:
    """Tek bir bar için stop/hedef değdi mi? (fiyat, "stop"/"tp") veya (None, "")."""
    if side == "long":
        if low <= stop:
            return stop, "stop"
        if high >= tp:
            return tp, "tp"
    else:  # short
        if high >= stop:
            return stop, "stop"
        if low <= tp:
            return tp, "tp"
    return None, ""


@dataclass
class OutcomeResult:
    """Bir sinyalin geçmiş veride çözülmüş sonucu."""

    outcome: str                 # WIN / LOSS / EXPIRED / OPEN
    exit_price: float | None
    exit_reason: str             # stop / tp / expired / open
    return_pct: float | None     # gerçekleşen getiri % (yön dahil)
    r_multiple: float | None     # getiri / planlanan risk
    bars_to_outcome: int | None  # kaç bar sonra çözüldü


def simulate_outcome(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    side: str,
    entry: float,
    stop: float,
    tp: float,
    max_bars: int,
) -> OutcomeResult:
    """Sinyal sonrası barlarda stop/hedef takibi yapıp sonucu döndürür.

    - İlk `max_bars` bar içinde stop değerse LOSS, hedef değerse WIN.
    - `max_bars` dolup hiçbiri değmediyse EXPIRED (son kapanışla işaretlenir).
    - Henüz `max_bars` kadar bar oluşmadıysa (yeterli gelecek yok) OPEN — sonra
      tekrar değerlendirilmek üzere açık bırakılır.
    """
    n = len(closes)
    risk_frac = abs(entry - stop) / entry if entry else 0.0
    horizon = min(n, max_bars)

    for i in range(horizon):
        ex, reason = check_bar_exit(side, float(highs[i]), float(lows[i]), stop, tp)
        if ex is not None:
            ret = (ex / entry - 1.0) if side == "long" else (entry / ex - 1.0)
            r = ret / risk_frac if risk_frac > 0 else 0.0
            outcome = "WIN" if ret > 0 else "LOSS"
            return OutcomeResult(
                outcome, round(ex, 6), reason, round(ret * 100.0, 4), round(r, 3), i + 1
            )

    if n >= max_bars:
        last_close = float(closes[max_bars - 1])
        ret = (last_close / entry - 1.0) if side == "long" else (entry / last_close - 1.0)
        r = ret / risk_frac if risk_frac > 0 else 0.0
        return OutcomeResult(
            "EXPIRED", round(last_close, 6), "expired", round(ret * 100.0, 4), round(r, 3), max_bars
        )

    return OutcomeResult("OPEN", None, "open", None, None, None)

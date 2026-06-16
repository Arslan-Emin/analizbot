"""Sinyali düz metne çevirme (Telegram ve loglar için paylaşılan biçimleyici)."""

from __future__ import annotations

from src.core.models import AnalysisResult

DISCLAIMER = (
    "UYARI: Yatırım tavsiyesi değildir. "
    "Sinyaller geçmiş veriye ve sabit kurallara dayanır."
)


def risk_reward(
    entry: float | None, stop: float | None, take_profit: float | None
) -> float | None:
    """R:R = potansiyel kazanç / potansiyel kayıp. Eksik veride None döner."""
    if entry is None or stop is None or take_profit is None:
        return None
    risk = abs(entry - stop)
    if risk == 0:
        return None
    return round(abs(take_profit - entry) / risk, 2)


def signal_to_text(result: AnalysisResult) -> str:
    """AnalysisResult'ı okunur düz metne çevirir."""
    s = result.signal
    ind = result.indicators
    lines: list[str] = []
    lines.append(f"{s.symbol} | {s.timeframe} | {s.created_at:%Y-%m-%d %H:%M} UTC")
    lines.append(f"Son fiyat: {ind.get('last_price')}")
    lines.append(f"SİNYAL: {s.action.value}  (güven %{s.confidence * 100:.0f})")
    lines.append("Gerekçeler:")
    for reason in s.reasons:
        lines.append(f"  • {reason}")
    lines.append(
        f"RSI {ind.get('rsi')} | EMA {ind.get('ema_fast')}/{ind.get('ema_slow')} | "
        f"MACD {ind.get('macd')}/{ind.get('macd_signal')} | ATR {ind.get('atr')}"
    )
    if ind.get("change_24h_pct") is not None:
        lines.append(
            f"24 bar değişim: %{ind['change_24h_pct']} | 24 bar hacim: {ind.get('volume_24h')}"
        )
    if s.stop_loss is not None:
        rr = risk_reward(s.suggested_entry, s.stop_loss, s.take_profit)
        lines.append(
            f"Giriş ~{s.suggested_entry} | Stop {s.stop_loss} | Hedef {s.take_profit} | R:R {rr}"
        )
        lines.append(f"Örnek boyut (varsayımsal): {s.suggested_size_quote} quote")
    lines.append(DISCLAIMER)
    return "\n".join(lines)

"""Konsol bildirimi — rich ile renkli, tablo biçimli rapor (spec §7)."""

from __future__ import annotations

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.core.models import Action, AnalysisResult
from src.notify.base import Notifier
from src.notify.format import DISCLAIMER, risk_reward

# Sinyale göre renk: al=yeşil, sat=kırmızı, bekle=sarı.
_ACTION_STYLE = {
    Action.BUY: "bold green",
    Action.SELL: "bold red",
    Action.HOLD: "bold yellow",
}


def _render(result: AnalysisResult) -> Panel:
    s = result.signal
    ind = result.indicators
    style = _ACTION_STYLE.get(s.action, "bold")

    # Başlık satırı
    header = Text()
    header.append(f"{s.symbol}", style="bold cyan")
    header.append(f"   {s.timeframe}   {s.created_at:%Y-%m-%d %H:%M} UTC\n")
    header.append(f"Son fiyat: {ind.get('last_price')}\n")
    header.append("SİNYAL: ")
    header.append(f"{s.action.value}", style=style)
    header.append(f"   güven %{s.confidence * 100:.0f}")

    # Gerekçeler
    reasons = Text("\nGerekçeler:\n", style="bold")
    for reason in s.reasons:
        reasons.append(f"  • {reason}\n")

    # İndikatör tablosu
    table = Table(show_header=True, header_style="bold magenta", expand=False)
    table.add_column("Gösterge")
    table.add_column("Değer", justify="right")
    table.add_row("RSI", str(ind.get("rsi")))
    table.add_row("EMA hızlı / yavaş", f"{ind.get('ema_fast')} / {ind.get('ema_slow')}")
    table.add_row("MACD / sinyal", f"{ind.get('macd')} / {ind.get('macd_signal')}")
    table.add_row("ATR", str(ind.get("atr")))
    # Ek osilatörler (yalnız snapshot'ta varsa göster — geriye dönük uyumlu).
    if ind.get("stoch_k") is not None:
        table.add_row("Stochastic %K", str(ind.get("stoch_k")))
    if ind.get("mfi") is not None:
        table.add_row("MFI", str(ind.get("mfi")))
    if ind.get("williams_r") is not None:
        table.add_row("Williams %R", str(ind.get("williams_r")))
    if ind.get("cci") is not None:
        table.add_row("CCI", str(ind.get("cci")))
    if ind.get("change_24h_pct") is not None:
        table.add_row("24 bar değişim", f"%{ind.get('change_24h_pct')}")
    table.add_row("24 bar hacim", str(ind.get("volume_24h")))

    parts = [header, reasons, table]

    # Öneri seviyeleri (sadece BUY/SELL'de)
    if s.stop_loss is not None:
        rr = risk_reward(s.suggested_entry, s.stop_loss, s.take_profit)
        levels = Text("\nÖneri seviyeleri (örnek/eğitsel):\n", style="bold")
        levels.append(
            f"  Giriş ~{s.suggested_entry}  |  Stop {s.stop_loss}  |  "
            f"Hedef {s.take_profit}  |  R:R {rr}\n"
        )
        levels.append(f"  Örnek pozisyon boyutu (varsayımsal): {s.suggested_size_quote} quote")
        parts.append(levels)

    parts.append(Text(f"\n{DISCLAIMER}", style="dim italic"))

    return Panel(Group(*parts), title="Analiz Raporu", border_style=style)


class ConsoleNotifier(Notifier):
    def __init__(self) -> None:
        self.console = Console()

    def send_signal(self, result: AnalysisResult) -> None:
        self.console.print(_render(result))

    def send_text(self, text: str) -> None:
        self.console.print(text)

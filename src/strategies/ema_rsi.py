"""Varsayılan strateji: EmaRsiStrategy (spec §6).

Kural-tabanlı, deterministik. EMA trend + RSI aşırılık + MACD onayını birleştirir.

Karar mantığı (skor = boğa onayı - ayı onayı):
  - BUY  : skor >= 2 VE RSI aşırı alımda değil VE (yukarı trend veya yukarı kesişim).
  - SELL : skor <= -2 VE (aşağı trend veya aşağı kesişim veya RSI aşırı alım).
  - HOLD : aksi (sinyaller zayıf/dengeli → "net değil").

Trend için küçük bir NÖTR BANT vardır: EMA'lar birbirine çok yakınsa (fiyatın
~%0.1'i içinde) "trend yok" sayılır; bu, yatay piyasada gürültüden sinyal
üretmeyi engeller ve HOLD'u gerçek bir sonuç yapar.

Güven skoru = sağlanan onay sayısı / toplam olası onay (yön başına 4).

NOT: Üretilen giriş/stop/hedef seviyeleri ÖRNEK/EĞİTSEL'dir; emir değildir.
"""

from __future__ import annotations

import pandas as pd

from src.core.indicators import compute_indicators, crossover, crossunder
from src.core.models import Action, Signal
from src.strategies.base import Strategy
from src.strategies.levels import compute_levels

# Yön başına toplam onay koşulu sayısı (güven skorunun paydası).
_MAX_CONFIRMATIONS = 4


class EmaRsiStrategy(Strategy):
    name = "ema_rsi"

    def __init__(self, params: dict) -> None:
        self.params = dict(params)

    def generate(self, df: pd.DataFrame, symbol: str) -> Signal:
        p = self.params
        ind = compute_indicators(df, p)
        last = ind.iloc[-1]
        prev = ind.iloc[-2] if len(ind) >= 2 else last

        ema_fast_n = int(p.get("ema_fast", 12))
        ema_slow_n = int(p.get("ema_slow", 26))
        overbought = float(p.get("rsi_overbought", 70))

        price = float(last["close"])
        atr_val = float(last["atr"])
        rsi_val = float(last["rsi"])

        # --- Trend: nötr bantlı (çok küçük EMA farkı = trend yok) ---
        ema_gap = float(last["ema_fast"] - last["ema_slow"])
        flat_thresh = price * float(p.get("trend_flat_pct", 0.001))
        trend_up = ema_gap > flat_thresh
        trend_down = ema_gap < -flat_thresh

        # --- Diğer ham koşullar ---
        cross_up = crossover(ind["ema_fast"], ind["ema_slow"])
        cross_down = crossunder(ind["ema_fast"], ind["ema_slow"])
        # Yatay piyasada (trend nötr bantta) EMA'lar birbirine değip geçer;
        # bu mikro-kesişimleri gürültü sayıp yok sayıyoruz (sahte sinyal önleme).
        if not trend_up and not trend_down:
            cross_up = False
            cross_down = False
        macd_bull = bool(last["macd"] > last["macd_signal"])
        macd_bear = bool(last["macd"] < last["macd_signal"])
        rsi_recovering = bool(rsi_val > float(prev["rsi"]) and rsi_val < 50.0)
        rsi_overbought = bool(rsi_val > overbought)
        rsi_ok = bool(rsi_val < overbought)  # aşırı alımda değil (BUY kapısının parçası)

        # --- Boğa (bullish) onayları + insan-okur gerekçeler ---
        bull: list[str] = []
        if trend_up:
            bull.append(f"EMA{ema_fast_n} > EMA{ema_slow_n}: yukarı trend")
        if cross_up:
            bull.append(f"EMA{ema_fast_n}, EMA{ema_slow_n}'yi yukarı kesti (taze al momentumu)")
        if macd_bull:
            bull.append("MACD sinyal çizgisinin üzerinde")
        if rsi_recovering:
            bull.append(f"RSI dipten yukarı dönüyor ({rsi_val:.0f})")

        # --- Ayı (bearish) onayları ---
        bear: list[str] = []
        if trend_down:
            bear.append(f"EMA{ema_fast_n} < EMA{ema_slow_n}: aşağı trend")
        if cross_down:
            bear.append(f"EMA{ema_fast_n}, EMA{ema_slow_n}'yi aşağı kesti (taze sat momentumu)")
        if macd_bear:
            bear.append("MACD sinyal çizgisinin altında")
        if rsi_overbought:
            bear.append(f"RSI aşırı alım bölgesinde ({rsi_val:.0f} > {overbought:.0f})")

        bull_count = len(bull)
        bear_count = len(bear)
        score = bull_count - bear_count

        # --- Karar ---
        if score >= 2 and rsi_ok and (trend_up or cross_up):
            action = Action.BUY
            reasons = [*bull, f"RSI aşırı alımda değil ({rsi_val:.0f} < {overbought:.0f})"]
            confidence = bull_count / _MAX_CONFIRMATIONS
            entry, stop, tp, size = self._levels(action, price, atr_val)
        elif score <= -2 and (trend_down or cross_down or rsi_overbought):
            action = Action.SELL
            reasons = list(bear)
            confidence = bear_count / _MAX_CONFIRMATIONS
            entry, stop, tp, size = self._levels(action, price, atr_val)
        else:
            action = Action.HOLD
            reasons = ["Net bir al/sat sinyali yok (göstergeler kararsız/dengeli)."]
            if bull:
                reasons.append("Zayıf alıcı sinyaller: " + "; ".join(bull))
            if bear:
                reasons.append("Zayıf satıcı sinyaller: " + "; ".join(bear))
            # Skor 0'a ne kadar yakınsa "beklemede kal" o kadar güvenli.
            confidence = 1.0 - abs(score) / _MAX_CONFIRMATIONS
            entry, stop, tp, size = price, None, None, None

        return Signal(
            symbol=symbol,
            action=action,
            confidence=round(min(max(confidence, 0.0), 1.0), 2),
            price=round(price, 2),
            reasons=reasons,
            suggested_entry=round(entry, 2) if entry is not None else None,
            stop_loss=stop,
            take_profit=tp,
            suggested_size_quote=size,
            timeframe=str(p.get("timeframe", "1h")),
        )

    def _levels(
        self, action: Action, price: float, atr_val: float
    ) -> tuple[float, float | None, float | None, float | None]:
        """ATR'ye dayalı örnek giriş/stop/hedef (paylaşılan yardımcıya delege eder)."""
        return compute_levels(action, price, atr_val, self.params)

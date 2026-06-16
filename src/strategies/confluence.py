"""ConfluenceStrategy — çok-koşullu, çok-zaman-dilimli (MTF) gelişmiş strateji.

ema_rsi'nin üstüne ekler:
  - ADX: trend GÜCÜ filtresi (zayıf trendde işlem açma) + yön (+DI/-DI).
  - Bollinger: fiyat alt/üst bantta mı (tepki/aşırılık bağlamı).
  - OBV (hacim): alıcı/satıcı baskısı yönü.
  - MTF onayı: AYNI veriyi üst zaman dilimine (örn 1h->4h) toplayıp ana trendi
    teyit eder. Üst TF ters yöndeyse işlem AÇMAZ (yanlış sinyali keser).

Karar: 7 olası onaydan skor (boğa-ayı). En az `min_confluence` (varsayılan 3)
onay + filtreler sağlanırsa BUY/SELL; aksi HOLD. Saf & deterministik (backtest-güvenli).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.core.indicators import (
    adx,
    bollinger,
    compute_indicators,
    crossover,
    crossunder,
    ema,
    mfi,
    obv,
    resample_ohlcv,
    supertrend,
)
from src.core.models import Action, Signal
from src.strategies.base import Strategy
from src.strategies.levels import compute_levels

# Çekirdek onay sayısı (güven paydası). Opsiyonel koşullar açılırsa payda dinamik artar.
_BASE_CONFIRMATIONS = 7


class ConfluenceStrategy(Strategy):
    name = "confluence"

    def __init__(self, params: dict) -> None:
        self.params = dict(params)

    def generate(self, df: pd.DataFrame, symbol: str) -> Signal:
        p = self.params
        ind = compute_indicators(df, p)
        last = ind.iloc[-1]

        price = float(last["close"])
        atr_val = float(last["atr"])
        rsi_val = float(last["rsi"])
        overbought = float(p.get("rsi_overbought", 70))
        rsi_ok = rsi_val < overbought

        # --- Trend (nötr bant) ---
        ema_fast_n = int(p.get("ema_fast", 12))
        ema_slow_n = int(p.get("ema_slow", 26))
        ema_gap = float(last["ema_fast"] - last["ema_slow"])
        flat = price * float(p.get("trend_flat_pct", 0.001))
        trend_up = ema_gap > flat
        trend_down = ema_gap < -flat

        cross_up = crossover(ind["ema_fast"], ind["ema_slow"])
        cross_down = crossunder(ind["ema_fast"], ind["ema_slow"])
        if not trend_up and not trend_down:  # yatayda mikro-kesişim gürültüsünü ele
            cross_up = cross_down = False

        macd_bull = bool(last["macd"] > last["macd_signal"])
        macd_bear = bool(last["macd"] < last["macd_signal"])

        # --- ADX: trend gücü + yön ---
        adx_line, plus_di, minus_di = adx(df, int(p.get("adx_period", 14)))
        adx_val = float(adx_line.iloc[-1])
        if np.isnan(adx_val):
            adx_val = 0.0
        strong_trend = adx_val >= float(p.get("adx_min", 20))
        di_bull = float(plus_di.iloc[-1]) > float(minus_di.iloc[-1])
        di_bear = float(minus_di.iloc[-1]) > float(plus_di.iloc[-1])

        # --- Bollinger bağlamı ---
        _, upper, lower = bollinger(
            ind["close"], int(p.get("bb_period", 20)), float(p.get("bb_std", 2.0))
        )
        bb_lower = float(lower.iloc[-1])
        bb_upper = float(upper.iloc[-1])
        near_lower = (not np.isnan(bb_lower)) and price <= bb_lower
        near_upper = (not np.isnan(bb_upper)) and price >= bb_upper

        # --- Hacim (OBV) yönü ---
        obv_series = obv(ind["close"], ind["volume"])
        obv_last = float(obv_series.iloc[-1])
        obv_prev = float(obv_series.iloc[-2]) if len(obv_series) >= 2 else obv_last
        obv_rising = obv_last > obv_prev
        obv_falling = obv_last < obv_prev

        # --- MTF: üst zaman dilimi trendi (aynı veriden resample) ---
        mtf_bull: bool | None = None
        mtf_bear: bool | None = None
        htf_rule = str(p.get("htf_rule", "4h"))
        if bool(p.get("use_mtf", True)):
            htf = resample_ohlcv(df, htf_rule)
            if len(htf) >= ema_slow_n + 2:
                hf = ema(htf["close"], ema_fast_n)
                hs = ema(htf["close"], ema_slow_n)
                mtf_bull = float(hf.iloc[-1]) > float(hs.iloc[-1])
                mtf_bear = float(hf.iloc[-1]) < float(hs.iloc[-1])

        # --- Onayları topla ---
        bull: list[str] = []
        if trend_up:
            bull.append(f"EMA{ema_fast_n}>EMA{ema_slow_n}: yukarı trend")
        if cross_up:
            bull.append("EMA yukarı kesişim (taze al momentumu)")
        if macd_bull:
            bull.append("MACD sinyalin üzerinde")
        if strong_trend and di_bull:
            bull.append(f"ADX güçlü trend ({adx_val:.0f}) + +DI baskın")
        if obv_rising:
            bull.append("OBV yukarı: alıcı hacmi")
        if near_lower:
            bull.append("Fiyat alt Bollinger bandında (tepki alımı)")
        if mtf_bull:
            bull.append(f"Üst TF ({htf_rule}) trendi yukarı")

        bear: list[str] = []
        if trend_down:
            bear.append(f"EMA{ema_fast_n}<EMA{ema_slow_n}: aşağı trend")
        if cross_down:
            bear.append("EMA aşağı kesişim (taze sat momentumu)")
        if macd_bear:
            bear.append("MACD sinyalin altında")
        if strong_trend and di_bear:
            bear.append(f"ADX güçlü trend ({adx_val:.0f}) + -DI baskın")
        if obv_falling:
            bear.append("OBV aşağı: satıcı hacmi")
        if near_upper:
            bear.append("Fiyat üst Bollinger bandında (aşırı alım)")
        if mtf_bear:
            bear.append(f"Üst TF ({htf_rule}) trendi aşağı")

        if rsi_val > overbought:
            bear.append(f"RSI aşırı alım ({rsi_val:.0f} > {overbought:.0f})")

        # --- Opsiyonel ek onaylar (config-gated; varsayılan KAPALI → davranış aynı) ---
        extra = 0
        if bool(p.get("use_supertrend", False)):
            extra += 1
            _, st_dir = supertrend(
                df,
                int(p.get("supertrend_period", 10)),
                float(p.get("supertrend_mult", 3.0)),
            )
            st = float(st_dir.iloc[-1])
            if st > 0:
                bull.append("Supertrend yukarı yönde")
            elif st < 0:
                bear.append("Supertrend aşağı yönde")
        if bool(p.get("use_mfi", False)):
            extra += 1
            mfi_val = float(mfi(df, int(p.get("mfi_period", 14))).iloc[-1])
            if not np.isnan(mfi_val):
                if mfi_val < float(p.get("mfi_oversold", 20)):
                    bull.append(f"MFI aşırı satım, tepki alımı ({mfi_val:.0f})")
                elif mfi_val > float(p.get("mfi_overbought", 80)):
                    bear.append(f"MFI aşırı alım ({mfi_val:.0f})")

        total = _BASE_CONFIRMATIONS + extra
        bull_count = len(bull)
        bear_count = len(bear)
        score = bull_count - bear_count
        min_conf = int(p.get("min_confluence", 3))

        # MTF bir FİLTREDİR: üst TF ters yöndeyse o yönde işlem açma.
        buy_ok = (
            score >= min_conf
            and rsi_ok
            and (trend_up or cross_up)
            and (mtf_bear is not True)
        )
        sell_ok = (
            score <= -min_conf
            and (trend_down or cross_down)
            and (mtf_bull is not True)
        )

        if buy_ok and bull_count > bear_count:
            action = Action.BUY
            reasons = [*bull, f"RSI aşırı alımda değil ({rsi_val:.0f} < {overbought:.0f})"]
            confidence = bull_count / total
            entry, stop, tp, size = compute_levels(action, price, atr_val, p)
        elif sell_ok and bear_count > bull_count:
            action = Action.SELL
            reasons = list(bear)
            confidence = bear_count / total
            entry, stop, tp, size = compute_levels(action, price, atr_val, p)
        else:
            action = Action.HOLD
            reasons = ["Yeterli onay yok / üst TF teyit etmiyor (göstergeler net değil)."]
            if bull:
                reasons.append("Zayıf alıcı: " + "; ".join(bull))
            if bear:
                reasons.append("Zayıf satıcı: " + "; ".join(bear))
            confidence = 1.0 - abs(score) / total
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

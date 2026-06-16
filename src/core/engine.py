"""Analiz motoru — veri sağlayıcı + strateji → AnalysisResult.

KRİTİK: Bu sınıf YALNIZCA arayüzlere (MarketDataProvider, Strategy) bağımlıdır;
somut Binance/ccxt sınıfını import ETMEZ. Piyasadan bağımsız çekirdek budur.
"""

from __future__ import annotations

import dataclasses
import logging

from src.core.indicators import cci, compute_indicators, mfi, stochastic, williams_r
from src.core.models import Action, AnalysisResult
from src.data.base import MarketDataProvider
from src.strategies.base import Strategy

log = logging.getLogger(__name__)


class AnalysisEngine:
    def __init__(
        self,
        provider: MarketDataProvider,
        strategy: Strategy,
        calibrator=None,
    ) -> None:
        self.provider = provider
        self.strategy = strategy
        # Opsiyonel güven kalibratörü (geçmiş isabete göre güveni ayarlar). None → kapalı.
        self.calibrator = calibrator

    def analyze(self, symbol: str, timeframe: str = "1h", limit: int = 500) -> AnalysisResult:
        # 1) Mum verisini çek
        df = self.provider.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

        # 2) Stratejiyi çalıştır → Signal. Gerçek timeframe'i sinyale yansıt
        #    (frozen dataclass olduğu için replace ile yeni bir kopya üretiyoruz).
        signal = self.strategy.generate(df, symbol)
        signal = dataclasses.replace(signal, timeframe=timeframe)
        signal = self._apply_calibration(signal)

        # 3) Rapor için son indikatör değerlerini topla
        indicators = self._indicator_snapshot(df, symbol)

        return AnalysisResult(
            signal=signal,
            indicators=indicators,
            market=self.provider.market,
            strategy=getattr(self.strategy, "name", "ema_rsi"),
        )

    def _apply_calibration(self, signal):
        """Kalibratör hazırsa BUY/SELL güvenini geçmiş isabete göre günceller."""
        if self.calibrator is None or not getattr(self.calibrator, "ready", False):
            return signal
        if signal.action not in (Action.BUY, Action.SELL):
            return signal
        calibrated = self.calibrator.calibrate(signal.confidence)
        reason = (
            f"Geçmiş isabet kalibrasyonu: ham %{signal.confidence * 100:.0f} → "
            f"%{calibrated * 100:.0f} (genel %{self.calibrator.hit_rate_pct:.0f}, "
            f"{self.calibrator.n_total} sinyal)"
        )
        return dataclasses.replace(
            signal,
            confidence=round(min(max(calibrated, 0.0), 1.0), 2),
            reasons=[*signal.reasons, reason],
        )

    def _indicator_snapshot(self, df, symbol: str) -> dict:
        """Raporda gösterilecek son bar indikatör değerleri + 24-bar özetleri."""
        params = getattr(self.strategy, "params", {})
        ind = compute_indicators(df, params)
        last = ind.iloc[-1]
        n = len(ind)

        # Son ~24 bar değişim/hacim (1h timeframe için ~24 saat).
        lookback = min(24, n - 1) if n > 1 else 0
        change_pct = None
        volume_window = None
        if lookback > 0:
            ref_close = float(ind["close"].iloc[-1 - lookback])
            if ref_close:
                change_pct = round((float(last["close"]) / ref_close - 1.0) * 100.0, 2)
            volume_window = round(float(ind["volume"].iloc[-lookback:].sum()), 2)

        # Anlık fiyat (mümkünse canlı ticker; olmazsa son kapanış).
        try:
            ticker_price = float(self.provider.get_ticker(symbol))
        except Exception as exc:  # ağ hatası analizi bozmasın
            log.debug("get_ticker başarısız, son kapanışa düşülüyor: %s", exc)
            ticker_price = float(last["close"])

        # Ek momentum osilatörleri (rapor zenginliği). Tek sembol/ucuz; NaN ise atla.
        extra = self._extra_oscillators(df)

        return {
            "last_price": round(ticker_price, 2),
            "last_close": round(float(last["close"]), 2),
            "rsi": round(float(last["rsi"]), 2),
            "ema_fast": round(float(last["ema_fast"]), 2),
            "ema_slow": round(float(last["ema_slow"]), 2),
            "macd": round(float(last["macd"]), 4),
            "macd_signal": round(float(last["macd_signal"]), 4),
            "atr": round(float(last["atr"]), 2),
            "volume_last": round(float(last["volume"]), 2),
            "volume_24h": volume_window,
            "change_24h_pct": change_pct,
            "bars": n,
            **extra,
        }

    @staticmethod
    def _extra_oscillators(df) -> dict:
        """Stochastic %K, MFI, Williams %R, CCI son değerleri (varsa)."""
        out: dict = {}
        try:
            k, _ = stochastic(df)
            pairs = {
                "stoch_k": k.iloc[-1],
                "mfi": mfi(df).iloc[-1],
                "williams_r": williams_r(df).iloc[-1],
                "cci": cci(df).iloc[-1],
            }
            for key, val in pairs.items():
                if val == val:  # NaN değilse (NaN != NaN)
                    out[key] = round(float(val), 2)
        except Exception as exc:  # rapor süsü; hata analizi bozmamalı
            log.debug("ek osilatör snapshot'ı atlandı: %s", exc)
        return out

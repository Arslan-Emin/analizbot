"""MlStrategy — eğitilmiş bir makine öğrenmesi modelinden sinyal üretir.

Model 'train' komutuyla eğitilip diske kaydedilir (models/ml_SEMBOL_TF.joblib).
Bu strateji o modeli yükler, SON bar için özellikleri hesaplar, sınıf olasılığı
tahmin eder. Güven skoru = en yüksek sınıf olasılığı. Model yoksa HOLD döner.

Strategy ABC'sini uygular → analyze/screen/backtest ile aynı şekilde kullanılır.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from src.core.indicators import atr
from src.core.models import Action, Signal
from src.ml.features import build_features
from src.ml.train import load_bundle, model_path
from src.strategies.base import Strategy
from src.strategies.levels import compute_levels

log = logging.getLogger(__name__)


class MlStrategy(Strategy):
    name = "ml"

    def __init__(self, params: dict) -> None:
        self.params = dict(params)
        self._cache: dict[str, dict | None] = {}  # sembol+tf -> bundle (tekrar yüklemeyi önler)

    def _get_bundle(self, symbol: str, timeframe: str) -> dict | None:
        key = f"{symbol}|{timeframe}"
        if key not in self._cache:
            path = model_path(self.params.get("model_dir", "models"), symbol, timeframe)
            self._cache[key] = load_bundle(path)
        return self._cache[key]

    def _hold(self, symbol: str, price: float, reason: str) -> Signal:
        return Signal(
            symbol=symbol,
            action=Action.HOLD,
            confidence=0.0,
            price=round(price, 2),
            reasons=[reason],
            timeframe=str(self.params.get("timeframe", "1h")),
        )

    def generate(self, df: pd.DataFrame, symbol: str) -> Signal:
        timeframe = str(self.params.get("timeframe", "1h"))
        price = float(df["close"].iloc[-1])

        bundle = self._get_bundle(symbol, timeframe)
        if bundle is None:
            return self._hold(
                symbol, price, f"ML modeli yok ({symbol} {timeframe}). Önce: train {symbol}"
            )

        feats = build_features(df, self.params)
        last = feats.iloc[[-1]][bundle["features"]]
        if last.isna().any(axis=1).iloc[0]:
            return self._hold(symbol, price, "Yetersiz veri (özellikler ısınmadı).")

        model = bundle["model"]
        proba = model.predict_proba(last)[0]
        classes = list(model.classes_)
        best = int(np.argmax(proba))
        action = Action(classes[best])
        confidence = float(proba[best])

        # Sınıf olasılıklarını okunur biçimde gerekçeye ekle.
        dist = ", ".join(f"{c}:%{p * 100:.0f}" for c, p in zip(classes, proba, strict=False))
        reasons = [
            f"ML tahmini: {action.value} (olasılık %{confidence * 100:.0f})",
            f"Sınıf dağılımı → {dist}",
            f"Model ufku: {bundle['horizon']} bar, eşik ±%{bundle['threshold_pct']}",
        ]

        if action == Action.HOLD:
            return Signal(
                symbol=symbol,
                action=action,
                confidence=round(confidence, 2),
                price=round(price, 2),
                reasons=reasons,
                timeframe=timeframe,
            )

        atr_val = float(atr(df, int(self.params.get("atr_period", 14))).iloc[-1])
        entry, stop, tp, size = compute_levels(action, price, atr_val, self.params)
        return Signal(
            symbol=symbol,
            action=action,
            confidence=round(confidence, 2),
            price=round(price, 2),
            reasons=reasons,
            suggested_entry=entry,
            stop_loss=stop,
            take_profit=tp,
            suggested_size_quote=size,
            timeframe=timeframe,
        )

"""Strateji arayüzü (spec §5.4).

Her strateji saf bir fonksiyon gibi davranmalı: (geçmiş veri) -> tek Signal.
Aynı girdi aynı çıktıyı vermeli (deterministik) — backtest ve birim testi
ancak böyle mümkün olur.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from src.core.models import Signal


class Strategy(ABC):
    name: str
    params: dict

    @abstractmethod
    def generate(self, df: pd.DataFrame, symbol: str) -> Signal:
        """Girdi: ham OHLCV DataFrame. Çıktı: tek bir Signal.

        Strateji kendi indikatörlerini hesaplar (compute_indicators).
        """

"""Veri sağlayıcı arayüzü (spec §5.2).

KRİTİK TASARIM KURALI: Strateji ve AnalysisEngine bu ARAYÜZE bağımlıdır,
somut Binance sınıfına değil. ABD borsası eklemek = yeni bir alt sınıf yazmak
(YFinanceData/IBKRData); çekirdek hiç değişmez.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class MarketDataProvider(ABC):
    """Tüm piyasalar bu soyut sınıfı uygular."""

    name: str    # "binance", ileride "ibkr" vb.
    market: str  # "crypto", ileride "us_equity"

    @abstractmethod
    def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 500
    ) -> pd.DataFrame:
        """Mum verisi döndürür.

        Kolonlar: open, high, low, close, volume.
        Index: UTC datetime (timestamp). En eski bar başta, en yeni bar sonda.
        """

    @abstractmethod
    def get_ticker(self, symbol: str) -> float:
        """Anlık son fiyat."""

    @abstractmethod
    def list_symbols(self) -> list[str]:
        """Bu piyasadaki işlem çiftleri/semboller."""

    def is_market_open(self) -> bool:
        """Kripto 7/24 açık → her zaman True.

        ABD adaptörü bunu override edip NYSE/Nasdaq takvimini uygular.
        """
        return True

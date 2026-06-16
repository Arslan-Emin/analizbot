"""AnalysisEngine uçtan-uca testi — sahte (mock) sağlayıcı ile, ağsız."""

from __future__ import annotations

import pandas as pd

from src.core.engine import AnalysisEngine
from src.core.models import Action, AnalysisResult
from src.data.base import MarketDataProvider
from src.strategies.ema_rsi import EmaRsiStrategy


class _MockProvider(MarketDataProvider):
    """Sabit DataFrame döndüren sahte sağlayıcı (ağ yok, deterministik)."""

    name = "mock"
    market = "crypto"

    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 500) -> pd.DataFrame:
        return self._df

    def get_ticker(self, symbol: str) -> float:
        return float(self._df["close"].iloc[-1])

    def list_symbols(self) -> list[str]:
        return ["TEST/USDT"]


def test_engine_returns_analysis_result(ohlcv, ema_rsi_params):
    engine = AnalysisEngine(_MockProvider(ohlcv), EmaRsiStrategy(ema_rsi_params))
    result = engine.analyze("TEST/USDT", timeframe="4h")

    assert isinstance(result, AnalysisResult)
    assert result.signal.symbol == "TEST/USDT"
    assert result.signal.action in (Action.BUY, Action.SELL, Action.HOLD)
    # Engine, çağrıdaki gerçek timeframe'i sinyale yansıtmalı
    assert result.signal.timeframe == "4h"
    assert result.market == "crypto"

    # Rapor için gerekli indikatör anahtarları mevcut olmalı
    for key in ["rsi", "ema_fast", "ema_slow", "macd", "macd_signal", "atr", "last_price"]:
        assert key in result.indicators

"""Sembol → veri sağlayıcı yönlendirmesi (genişletilebilirlik dikişi).

Şu an her sembol Binance'e gider. ABD borsası eklenince, sembol desenine
göre (örn. "AAPL" → us_equity) doğru sağlayıcıya yönlendirme YALNIZCA burada
yapılır. Böylece çekirdeğe `if market == "crypto"` mantığı sızmaz.
"""

from __future__ import annotations

from src.data.base import MarketDataProvider
from src.data.crypto_ccxt import CcxtBinanceData


def get_provider(
    symbol: str,
    *,
    api_key: str | None = None,
    api_secret: str | None = None,
) -> MarketDataProvider:
    """Verilen sembol için uygun MarketDataProvider'ı döndürür."""
    # İleride: if _looks_like_us_equity(symbol): return YFinanceData(...)
    return CcxtBinanceData(api_key=api_key, api_secret=api_secret)

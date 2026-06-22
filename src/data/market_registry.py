"""Sembol → veri sağlayıcı yönlendirmesi (genişletilebilirlik dikişi).

Şu an her sembol Binance'e gider. ABD borsası eklenince, sembol desenine
göre (örn. "AAPL" → us_equity) doğru sağlayıcıya yönlendirme YALNIZCA burada
yapılır. Böylece çekirdeğe `if market == "crypto"` mantığı sızmaz.
"""

from __future__ import annotations

import re

from src.data.base import MarketDataProvider
from src.data.crypto_ccxt import CcxtBinanceData

# Crypto sembolleri 'BASE/QUOTE' biçimindedir ('/'). ABD hissesi/endeksi: AAPL,
# BRK-B, BRK.B, ^GSPC, ^VIX gibi (opsiyonel '^' önekli, harf+nokta/tire).
_US_EQUITY_RE = re.compile(r"^\^?[A-Z][A-Z.\-]{0,5}$")


def _looks_like_us_equity(symbol: str) -> bool:
    """Sembol bir ABD hissesi/endeksi gibi mi görünüyor? ('/' yoksa ve desene uyuyorsa)."""
    return "/" not in symbol and bool(_US_EQUITY_RE.match(symbol.upper()))


def get_provider(
    symbol: str,
    *,
    api_key: str | None = None,
    api_secret: str | None = None,
) -> MarketDataProvider:
    """Verilen sembol için uygun MarketDataProvider'ı döndürür (sembol desenine göre)."""
    if _looks_like_us_equity(symbol):
        from src.data.yfinance_data import YFinanceData

        return YFinanceData()
    return CcxtBinanceData(api_key=api_key, api_secret=api_secret)

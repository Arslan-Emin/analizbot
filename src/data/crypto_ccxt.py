"""Binance somut veri sağlayıcısı (ccxt ile).

Yalnızca OKUR: OHLCV + ticker çeker. API anahtarı opsiyoneldir; verilirse
SADECE read-only kullanılır (gerçek emir gönderme kodu YOKTUR). Ağ/borsa
hataları tenacity ile exponential-backoff yapılarak yeniden denenir.
"""

from __future__ import annotations

import logging

import ccxt
import pandas as pd
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.data.base import MarketDataProvider

log = logging.getLogger(__name__)

# Geçici/yeniden-denenebilir hatalar (ağ, zaman aşımı, rate-limit, DDoS koruması).
_RETRYABLE_ERRORS = (
    ccxt.NetworkError,
    ccxt.RequestTimeout,
    ccxt.DDoSProtection,
    ccxt.RateLimitExceeded,
)

_OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

# Ortak retry politikası: en fazla 5 deneme, 1s→30s arası katlanan bekleme.
# reraise=True → tüm denemeler tükenirse orijinal hatayı yükselt (sarmalamadan).
_retry_network = retry(
    retry=retry_if_exception_type(_RETRYABLE_ERRORS),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)


class CcxtBinanceData(MarketDataProvider):
    """ccxt üzerinden Binance spot verisi."""

    name = "binance"
    market = "crypto"

    def __init__(self, api_key: str | None = None, api_secret: str | None = None) -> None:
        config: dict = {"enableRateLimit": True}  # ccxt'nin kendi rate-limit'i açık
        # Anahtar OPSİYONEL: genel piyasa verisi için gerekmez. Verilirse read-only.
        if api_key and api_secret:
            config["apiKey"] = api_key
            config["secret"] = api_secret
        # NOT: config'i ASLA loglama (apiKey/secret sızdırmamak için).
        self._exchange = ccxt.binance(config)

    @_retry_network
    def _fetch_ohlcv_raw(self, symbol: str, timeframe: str, limit: int) -> list[list]:
        # ccxt çıktısı: her bar [ms_timestamp, open, high, low, close, volume]
        return self._exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 500
    ) -> pd.DataFrame:
        raw = self._fetch_ohlcv_raw(symbol, timeframe, limit)
        df = pd.DataFrame(raw, columns=_OHLCV_COLUMNS)
        # ms epoch -> UTC datetime ve index yap (zaman serisi için doğal anahtar).
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("timestamp").astype(float)
        return df

    @_retry_network
    def get_ticker(self, symbol: str) -> float:
        ticker = self._exchange.fetch_ticker(symbol)
        return float(ticker["last"])

    @_retry_network
    def list_symbols(self) -> list[str]:
        markets = self._exchange.load_markets()
        return sorted(markets.keys())

    @_retry_network
    def _fetch_ohlcv_page(
        self, symbol: str, timeframe: str, since_ms: int, page_limit: int
    ) -> list[list]:
        return self._exchange.fetch_ohlcv(
            symbol, timeframe=timeframe, since=since_ms, limit=page_limit
        )

    def fetch_ohlcv_range(
        self,
        symbol: str,
        timeframe: str,
        since_ms: int,
        until_ms: int,
        page_limit: int = 1000,
    ) -> pd.DataFrame:
        """Tarih aralığı için sayfalı (paginated) OHLCV çeker (backtest için).

        Binance tek istekte ~1000 bar verir; aralığı tamamlamak için `since`'i
        her sayfada ilerletiriz. ABC'de değildir — geçmiş veri çekme uzantısıdır.
        """
        timeframe_ms = int(self._exchange.parse_timeframe(timeframe) * 1000)
        rows: list[list] = []
        since = since_ms
        while since < until_ms:
            batch = self._fetch_ohlcv_page(symbol, timeframe, since, page_limit)
            if not batch:
                break
            rows.extend(batch)
            since = batch[-1][0] + timeframe_ms  # bir sonraki bardan devam et
            if len(batch) < page_limit:
                break  # borsa daha fazla veri vermedi

        df = pd.DataFrame(rows, columns=_OHLCV_COLUMNS)
        if df.empty:
            return df.set_index("timestamp")
        df = df[df["timestamp"] < until_ms]  # üst sınırı uygula
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df.set_index("timestamp").astype(float)

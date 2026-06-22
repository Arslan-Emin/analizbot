"""ABD hisse senedi veri sağlayıcısı (yfinance ile).

KRİTİK TASARIM: MarketDataProvider arayüzünü uygular → çekirdek motor, stratejiler
ve backtest HİÇ DEĞİŞMEDEN hisse senetlerinde çalışır (crypto ile aynı Signal/akış).
Bu, financial-services ve tradermonty hisse-odaklı skillerinin (CANSLIM, earnings,
13F) önünü açar.

Yalnızca OKUR (yfinance halka açık veri). timeframe → yfinance interval eşlenir;
desteklenmeyen periyotta açık hata verir. Üretilen veri OHLCV: open/high/low/close/volume,
UTC DatetimeIndex.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from src.data.base import MarketDataProvider

log = logging.getLogger(__name__)

# Crypto-stili timeframe → yfinance interval. 4h gibi desteklenmeyenler için hata.
_INTERVAL_MAP = {
    "1m": "1m", "2m": "2m", "5m": "5m", "15m": "15m", "30m": "30m",
    "60m": "60m", "1h": "1h", "90m": "90m",
    "1d": "1d", "5d": "5d", "1wk": "1wk", "1mo": "1mo", "3mo": "3mo",
}

# Varsayılan hisse evreni (list_symbols / breadth için; config ile genişletilebilir).
# Likit ABD büyük-sermayeleri — screen ve breadth bu listeyi tarar.
DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "BRK-B", "JPM",
    "V", "MA", "UNH", "HD", "PG", "JNJ", "COST", "WMT", "XOM", "CVX",
    "LLY", "ABBV", "MRK", "KO", "PEP", "BAC", "NFLX", "AMD", "CRM", "ADBE",
]

_NY_TZ = ZoneInfo("America/New_York")


class YFinanceData(MarketDataProvider):
    """yfinance üzerinden ABD hisse verisi (OHLCV + son fiyat)."""

    name = "yfinance"
    market = "us_equity"

    def __init__(self, universe: list[str] | None = None) -> None:
        self._universe = universe or DEFAULT_UNIVERSE

    @staticmethod
    def _interval(timeframe: str) -> str:
        try:
            return _INTERVAL_MAP[timeframe]
        except KeyError as exc:
            raise ValueError(
                f"yfinance bu timeframe'i desteklemiyor: {timeframe!r}. "
                f"Hisse için 1d/1h/1wk kullanın (desteklenen: {sorted(_INTERVAL_MAP)})."
            ) from exc

    @staticmethod
    def _period_for(interval: str) -> str:
        if interval in ("1d", "5d", "1wk", "1mo", "3mo"):
            return "max"
        if interval in ("1h", "60m", "90m", "30m"):
            return "730d"  # yfinance saatlik üst sınırı
        return "60d"  # dakikalık veriler için daha kısa pencere

    @staticmethod
    def _normalize(raw: pd.DataFrame) -> pd.DataFrame:
        """yfinance çıktısını standart OHLCV'ye çevirir (UTC index, küçük harf kolonlar)."""
        if raw is None or raw.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = raw.copy()
        # Tek sembolde bile bazı sürümler MultiIndex kolon döndürür → düzleştir.
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=str.lower)
        cols = ["open", "high", "low", "close", "volume"]
        df = df[[c for c in cols if c in df.columns]].astype(float)
        # Index'i UTC'ye çek (günlük veri tz-naive gelir → UTC olarak işaretle).
        idx = pd.DatetimeIndex(df.index)
        df.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        df.index.name = "timestamp"
        return df.dropna()

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1d", limit: int = 500) -> pd.DataFrame:
        import yfinance as yf

        interval = self._interval(timeframe)
        raw = yf.download(
            symbol, period=self._period_for(interval), interval=interval,
            auto_adjust=True, progress=False, threads=False,
        )
        df = self._normalize(raw)
        return df.tail(limit) if limit and len(df) > limit else df

    def fetch_ohlcv_range(
        self, symbol: str, timeframe: str, since_ms: int, until_ms: int
    ) -> pd.DataFrame:
        """Backtest/optimize için tarih aralığı (crypto sağlayıcısıyla aynı imza)."""
        import yfinance as yf

        interval = self._interval(timeframe)
        start = pd.to_datetime(since_ms, unit="ms", utc=True)
        end = pd.to_datetime(until_ms, unit="ms", utc=True)
        raw = yf.download(
            symbol, start=start.date(), end=end.date(), interval=interval,
            auto_adjust=True, progress=False, threads=False,
        )
        return self._normalize(raw)

    def get_ticker(self, symbol: str) -> float:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        try:
            price = ticker.fast_info["lastPrice"]
            if price:
                return float(price)
        except Exception as exc:  # fast_info bazı sembollerde yok → geçmişe düş
            log.debug("fast_info başarısız (%s): %s", symbol, exc)
        hist = ticker.history(period="1d")
        return float(hist["Close"].iloc[-1])

    def list_symbols(self) -> list[str]:
        return list(self._universe)

    def is_market_open(self) -> bool:
        """Kaba NYSE kontrolü: hafta içi 09:30–16:00 ET (resmi tatiller HARİÇ değil)."""
        now = datetime.now(_NY_TZ)
        if now.weekday() >= 5:  # Cmt/Pzr
            return False
        minutes = now.hour * 60 + now.minute
        return 9 * 60 + 30 <= minutes <= 16 * 60

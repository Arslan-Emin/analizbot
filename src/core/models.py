"""Çekirdek veri modelleri (spec §5.1).

Bu modeller piyasadan bağımsızdır: hem kripto hem ileride ABD borsası
aynı `Signal`/`AnalysisResult` tiplerini kullanır.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum


class Action(str, Enum):  # noqa: UP042  (spec §5.1 birebir str+Enum istiyor)
    """Üretilebilecek üç sinyal. `str` Enum olduğu için JSON/DB'ye yazımı kolaydır."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


def _now_utc() -> datetime:
    # created_at için varsayılan üretici. UTC kullanıyoruz ki kayıtlar
    # makineden bağımsız ve karşılaştırılabilir olsun.
    return datetime.now(UTC)


@dataclass(frozen=True)
class Signal:
    """Tek bir analiz çıktısı. `frozen=True` → üretildikten sonra değişmez (immutable).

    Bu, sinyalin "kanıt" niteliğini korur: bir kez oluştu mu, sonradan
    sessizce değiştirilemez.
    """

    symbol: str
    action: Action
    confidence: float                       # 0.0 - 1.0 arası güven skoru
    price: float                            # sinyal anındaki son fiyat
    reasons: list[str]                      # insan-okur gerekçeler ("neden")
    suggested_entry: float | None = None    # önerilen giriş bölgesi
    stop_loss: float | None = None          # önerilen zarar-durdur
    take_profit: float | None = None        # önerilen kâr-al
    suggested_size_quote: float | None = None  # varsayımsal sermayeye göre örnek boyut (quote)
    timeframe: str = "1h"
    # frozen dataclass'ta değişebilir/dinamik varsayılan için default_factory şarttır
    # (aksi halde tüm örnekler aynı zamanı paylaşırdı).
    created_at: datetime = field(default_factory=_now_utc)


@dataclass
class AnalysisResult:
    """Sinyal + o anki ham indikatör değerleri. Raporlama/depolama bunu kullanır."""

    signal: Signal
    indicators: dict                # {"rsi": 58.2, "ema_fast": ..., ...}
    market: str = "crypto"          # ileride "us_equity" olabilir
    strategy: str = "ema_rsi"       # sinyali üreten strateji (geri besleme/öğrenme için)

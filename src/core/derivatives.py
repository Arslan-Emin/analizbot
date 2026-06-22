"""Türev (perpetual) verisi: funding rate + open interest yorumlama.

NEDEN? Kripto'da funding rate ve open interest, pozisyonlanma/duygu sinyalidir
ve fiyat-tabanlı indikatörlerin göremediği bir boyut ekler:
  - Aşırı pozitif funding = kalabalık long = aşırı ısınma → contrarian risk ↓.
  - Aşırı negatif funding = kalabalık short → short-sıkışması (squeeze) riski ↑.
  - OI + fiyat birlikte artıyorsa trend YENİ parayla destekleniyor (teyit).

Veri Binance'te ÜCRETSİZDİR ama yalnız PERPETUAL sembolde bulunur (spotta yok).
KISIT: Binance open interest GEÇMİŞİ ~30 günle sınırlıdır; funding geçmişi yıllarca
geriye gider. Bu yüzden ML özelliği olarak yalnız `funding_rate` gerçekçidir; OI
canlı bir duygu göstergesi olarak raporlanır.
"""

from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)


def to_perp_symbol(symbol: str) -> str:
    """Spot sembolü USDM perpetual'a çevirir: 'BTC/USDT' → 'BTC/USDT:USDT'.

    Zaten perpetual (':' içeriyor) ise olduğu gibi döndürür. Beklenmedik biçimde
    de dokunmadan döndürür (çağıran taraf güvenle deneyebilsin).
    """
    if ":" in symbol or "/" not in symbol:
        return symbol
    quote = symbol.split("/", 1)[1]
    return f"{symbol}:{quote}"


def funding_sentiment(rate: float, cfg: dict | None = None) -> tuple[str, str]:
    """Funding rate'i contrarian duyguya çevirir → (etiket, açıklama).

    `rate` ccxt kesir biçimindedir (0.0001 = %0.01 / 8 saat).
    """
    cfg = cfg or {}
    elevated = float(cfg.get("funding_elevated", 0.0003))
    extreme = float(cfg.get("funding_extreme", 0.0007))
    pct8h = rate * 100.0
    ann = rate * 3 * 365 * 100.0  # 8s → yıllık kabaca

    if rate >= extreme:
        return "AŞIRI_LONG", (
            f"Funding çok yüksek (%{pct8h:.3f}/8s ≈ %{ann:.0f}/yıl): kalabalık long, "
            f"aşırı ısınma → contrarian temkin (düzeltme riski)"
        )
    if rate >= elevated:
        return "LONG_AĞIRLIKLI", f"Funding yüksek (%{pct8h:.3f}/8s): long baskın, temkinli ol"
    if rate <= -extreme:
        return "AŞIRI_SHORT", (
            f"Funding çok negatif (%{pct8h:.3f}/8s): kalabalık short → "
            f"short-sıkışması (squeeze) riski ↑"
        )
    if rate <= -elevated:
        return "SHORT_AĞIRLIKLI", f"Funding negatif (%{pct8h:.3f}/8s): short baskın"
    return "NÖTR", f"Funding nötr (%{pct8h:.3f}/8s)"


def oi_trend(oi_values: list[float]) -> tuple[str, float]:
    """Open interest eğilimi: son değerin ilk değere göre % değişimi → (etiket, %)."""
    vals = [float(v) for v in oi_values if v is not None and v == v]  # NaN ele
    if len(vals) < 2 or vals[0] == 0:
        return "BİLİNMİYOR", 0.0
    change = (vals[-1] / vals[0] - 1.0) * 100.0
    if change > 5.0:
        return "ARTIYOR", round(change, 1)
    if change < -5.0:
        return "AZALIYOR", round(change, 1)
    return "YATAY", round(change, 1)


def align_history_to_index(hist: pd.Series, target_index: pd.DatetimeIndex) -> pd.Series:
    """Düzensiz zamanlı geçmişi (funding 8s, OI vb.) hedef bar index'ine hizalar.

    Her hedef bar için o bara KADAR bilinen son değeri (ffill) verir → LOOK-AHEAD YOK.
    (Funding değeri ancak settlement zamanından itibaren bilinir; ffill bunu korur.)
    """
    if hist is None or len(hist) == 0:
        return pd.Series(index=target_index, dtype=float)
    h = hist.sort_index()
    combined = h.reindex(h.index.union(target_index)).ffill()
    return combined.reindex(target_index)


def merge_funding_history(df: pd.DataFrame, provider, symbol: str, params: dict) -> pd.DataFrame:
    """df'e look-ahead'siz `funding_rate` kolonu ekler (ML eğitimi için).

    Funding geçmişini perpetual sembolden çeker, df.index'e hizalar. Veri yoksa
    veya sağlayıcı desteklemiyorsa df'i DEĞİŞMEDEN döndürür (zarif düşüş).
    """
    if not hasattr(provider, "fetch_funding_rate_history") or len(df) == 0:
        return df
    perp = to_perp_symbol(symbol)
    try:
        since_ms = int(df.index[0].timestamp() * 1000)
        raw = provider.fetch_funding_rate_history(perp, since_ms=since_ms, limit=1000)
    except Exception as exc:
        log.warning("Funding geçmişi alınamadı (%s): %s", perp, exc)
        return df
    if not raw:
        return df

    ts = pd.to_datetime([r.get("timestamp") for r in raw], unit="ms", utc=True)
    rates = pd.Series([float(r.get("fundingRate") or 0.0) for r in raw], index=ts)
    out = df.copy()
    out["funding_rate"] = align_history_to_index(rates, df.index).fillna(0.0)
    return out


def derivatives_snapshot(provider, symbol: str, cfg: dict | None = None) -> dict:
    """Canlı funding rate + open interest özetini döndürür (rapor için).

    Hata/destek yoksu durumunda kısmi ya da boş sözlük döner (analizi BOZMAZ).
    """
    cfg = cfg or {}
    perp = to_perp_symbol(symbol)
    snap: dict = {"perp_symbol": perp}

    if cfg.get("use_funding", True) and hasattr(provider, "fetch_funding_rate"):
        try:
            fr = provider.fetch_funding_rate(perp)
            rate = fr.get("fundingRate")
            if rate is not None:
                rate = float(rate)
                snap["funding_rate"] = rate
                snap["funding_sentiment"] = funding_sentiment(rate, cfg)
        except Exception as exc:
            log.debug("funding snapshot atlandı (%s): %s", perp, exc)

    if cfg.get("use_open_interest", True) and hasattr(provider, "fetch_open_interest"):
        try:
            oi = provider.fetch_open_interest(perp)
            amount = oi.get("openInterestAmount") or oi.get("openInterestValue")
            if amount is not None:
                snap["open_interest"] = float(amount)
        except Exception as exc:
            log.debug("open interest snapshot atlandı (%s): %s", perp, exc)

        # OI eğilimi (son ~30 bar; Binance geçmiş kısıtı).
        if hasattr(provider, "fetch_open_interest_history"):
            try:
                hist = provider.fetch_open_interest_history(
                    perp,
                    timeframe=str(cfg.get("oi_history_timeframe", "8h")),
                    limit=int(cfg.get("oi_history_limit", 30)),
                )
                vals = [h.get("openInterestAmount") or h.get("openInterestValue") for h in hist]
                if len([v for v in vals if v]) >= 2:
                    snap["oi_trend"] = oi_trend(vals)
            except Exception as exc:
                log.debug("OI geçmişi atlandı (%s): %s", perp, exc)

    return snap

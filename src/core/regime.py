"""Piyasa rejimi tespiti + sinyal kapılama (regime gating).

NEDEN? Sinyaller tek başına üretildiğinde makro bağlamı kaçırır: ayı rejiminde
üretilen BUY veya boğa rejiminde üretilen SELL istatistiksel olarak daha düşük
isabetlidir. Bu modül piyasayı RISK_ON / NEUTRAL / RISK_OFF olarak sınıflar ve
karşı-rejim sinyallerinin güvenini düşürür (soft) ya da eler (gate).

İlham: tradermonty/claude-trading-skills — macro-regime-detector,
market-breadth-analyzer, exposure-coach, market-top-detector.

TASARIM (look-ahead güvenli):
  - `assess_trend_regime(df, cfg)`: SAF fonksiyon. Bir benchmark OHLCV'sinden trend
    rejimini çıkarır (fiyat vs uzun-EMA + EMA eğimi + ADX yön/güç). Yalnızca verilen
    df'in son barına bakar → backtest'te bar-bar güvenle çağrılabilir.
  - `compute_breadth(provider, ...)`: CANLI breadth (sembollerin % kaçı MA üstünde).
  - `build_live_regime(provider, cfg, symbols)`: benchmark + breadth → tek değerlendirme.
  - `gate_signal(...)`: rejime göre (action, confidence) ayarı.
  - `make_backtest_regime_fn(benchmark_daily, cfg)`: backtest için günlük rejimleri
    önceden hesaplar; her intraday bar için KAPANMIŞ son günlük rejimi döndürür
    (look-ahead yok). Sarmalayıcı `RegimeFilteredStrategy` bunu kullanır.

config.yaml `regime:` bölümündeki anahtarlar ÖNEKSİZ okunur (örn. `trend_period`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from src.core.indicators import adx, ema
from src.core.models import Action

log = logging.getLogger(__name__)


class RegimeState(str, Enum):  # noqa: UP042  (Action ile tutarlı: str+Enum, JSON/DB kolaylığı)
    """Üç piyasa rejimi. `str` Enum → JSON/rapor yazımı kolay."""

    RISK_ON = "RISK_ON"     # boğa: long-yanlı, tam maruziyet
    NEUTRAL = "NEUTRAL"     # belirsiz/yatay: kapılama yok
    RISK_OFF = "RISK_OFF"   # ayı: short-yanlı, maruziyeti kıs


@dataclass(frozen=True)
class RegimeAssessment:
    """Piyasa rejimi değerlendirmesi (immutable)."""

    state: RegimeState
    score: float                      # -1.0 (tam ayı) .. +1.0 (tam boğa)
    position_bias: str                # "long" | "short" | "flat"
    exposure_ceiling: float           # 0.0 .. 1.0 — önerilen azami maruziyet
    breadth_pct: float | None = None  # canlı breadth (% MA üstünde) veya None
    reasons: list[str] = field(default_factory=list)
    ready: bool = True                # False → yeterli veri yok, kapılama yapma


# --------------------------------------------------------------------------- #
# Saf çekirdek: trend rejimi (backtest + canlı paylaşır)
# --------------------------------------------------------------------------- #


def _score_to_state(score: float, cfg: dict) -> tuple[RegimeState, str, float]:
    """Sürekli skoru (-1..+1) ayrık duruma + bias + maruziyet tavanına eşler."""
    on_th = float(cfg.get("risk_on_score", 0.34))
    off_th = float(cfg.get("risk_off_score", -0.34))
    if score >= on_th:
        return RegimeState.RISK_ON, "long", float(cfg.get("risk_on_ceiling", 1.0))
    if score <= off_th:
        return RegimeState.RISK_OFF, "short", float(cfg.get("risk_off_ceiling", 0.25))
    return RegimeState.NEUTRAL, "flat", float(cfg.get("neutral_ceiling", 0.6))


def assess_trend_regime(df: pd.DataFrame, cfg: dict) -> RegimeAssessment:
    """Bir benchmark OHLCV'sinden trend rejimini çıkarır (SAF, look-ahead yok).

    Bileşenler (ağırlıklı skor, [-1, +1] kırpılır):
      - Fiyat uzun-EMA'nın üstünde/altında (±0.5)
      - Uzun-EMA eğimi yukarı/aşağı (±0.2)
      - ADX güçlü trendde +DI/-DI yönü (±0.3)
    Yeterli bar yoksa NEUTRAL + ready=False (kapılama devre dışı).
    """
    trend_period = int(cfg.get("trend_period", 200))
    close = df["close"]
    n = len(close)
    if n < trend_period + 5:
        return RegimeAssessment(
            RegimeState.NEUTRAL, 0.0, "flat", float(cfg.get("neutral_ceiling", 0.6)),
            None, [f"Rejim için yetersiz veri ({n} bar < {trend_period}+5)"], ready=False,
        )

    long_ema = ema(close, trend_period)
    price = float(close.iloc[-1])
    ema_now = float(long_ema.iloc[-1])

    # EMA eğimi: son ~%5'lik pencere üzerinden yön.
    slope_lb = max(5, trend_period // 20)
    ema_prev = float(long_ema.iloc[-1 - slope_lb])
    ema_slope = (ema_now - ema_prev) / ema_prev if ema_prev else 0.0

    adx_line, plus_di, minus_di = adx(df, int(cfg.get("adx_period", 14)))
    adx_val = float(adx_line.iloc[-1])
    if np.isnan(adx_val):
        adx_val = 0.0
    di_bull = float(plus_di.iloc[-1]) >= float(minus_di.iloc[-1])
    adx_min = float(cfg.get("adx_min", 20.0))

    dist_pct = (price / ema_now - 1.0) * 100.0 if ema_now else 0.0

    # Fiyat-EMA mesafesi SÜREKLİ terim (±0.5): EMA'ya yakın → ~0 (yatay/NEUTRAL),
    # `trend_dist_sat_pct` kadar uzakta → doygunluk (±0.5). İkili eşik yerine bu,
    # gerçek yatay piyasaların NEUTRAL kalmasını sağlar.
    sat = float(cfg.get("trend_dist_sat_pct", 5.0))
    if sat > 0:
        price_term = max(-1.0, min(1.0, dist_pct / sat)) * 0.5
    else:
        price_term = 0.5 if dist_pct > 0 else -0.5

    score = price_term
    reasons: list[str] = []
    reasons.append(
        f"Fiyat {trend_period}-EMA'ya göre %{dist_pct:+.1f} "
        f"({'üstünde' if dist_pct >= 0 else 'altında'})"
    )
    if ema_slope > 0:
        score += 0.2
        reasons.append(f"{trend_period}-EMA eğimi yukarı (%{ema_slope * 100:+.2f})")
    elif ema_slope < 0:
        score -= 0.2
        reasons.append(f"{trend_period}-EMA eğimi aşağı (%{ema_slope * 100:+.2f})")
    if adx_val >= adx_min:
        score += 0.3 if di_bull else -0.3
        reasons.append(
            f"ADX güçlü trend ({adx_val:.0f} ≥ {adx_min:.0f}), "
            f"{'+DI' if di_bull else '-DI'} baskın"
        )
    else:
        reasons.append(f"ADX zayıf/yatay ({adx_val:.0f} < {adx_min:.0f})")

    score = max(-1.0, min(1.0, score))
    state, bias, ceiling = _score_to_state(score, cfg)
    return RegimeAssessment(state, round(score, 3), bias, ceiling, None, reasons)


# --------------------------------------------------------------------------- #
# Canlı breadth + birleşik canlı rejim
# --------------------------------------------------------------------------- #


def compute_breadth(
    provider,
    symbols: list[str],
    timeframe: str = "1d",
    ma_period: int = 50,
    limit: int = 120,
) -> float | None:
    """Sembollerin yüzde kaçının son kapanışı kendi `ma_period` SMA'sının üstünde?

    Risk-on/off ölçüsü: >%60 sağlıklı katılım, <%40 zayıf (dağıtım). Hata veren
    sembol atlanır. Hiç sembol çözülmezse None.
    """
    above = 0
    total = 0
    for sym in symbols:
        try:
            df = provider.fetch_ohlcv(sym, timeframe=timeframe, limit=limit)
            if len(df) < ma_period + 1:
                continue
            ma_last = float(df["close"].rolling(ma_period).mean().iloc[-1])
            if ma_last != ma_last:  # NaN
                continue
            if float(df["close"].iloc[-1]) > ma_last:
                above += 1
            total += 1
        except Exception as exc:  # tek sembol hatası breadth'i bozmasın
            log.debug("breadth: %s atlandı: %s", sym, exc)
    if total == 0:
        return None
    return round(100.0 * above / total, 1)


def select_breadth_symbols(provider, quote: str = "USDT", n: int = 30) -> list[str]:
    """Breadth için sembol seç: mümkünse hacme göre, değilse alfabetik ilk N.

    `top_symbols_by_volume` sağlayıcıda varsa (CcxtBinanceData) likiditeye göre
    sıralanır; yoksa spot çiftlerin alfabetik ilk N'i (dokümante kısıt).
    """
    if hasattr(provider, "top_symbols_by_volume"):
        try:
            return provider.top_symbols_by_volume(quote=quote, n=n)
        except Exception as exc:
            log.debug("top_symbols_by_volume başarısız, alfabetiğe düşülüyor: %s", exc)
    suffix = "/" + quote.upper()
    syms = [s for s in provider.list_symbols() if ":" not in s and s.endswith(suffix)]
    return syms[:n]


def build_live_regime(provider, cfg: dict, symbols: list[str] | None = None) -> RegimeAssessment:
    """Canlı rejim: benchmark trendi (BTC günlük) + opsiyonel breadth harmanı."""
    benchmark = str(cfg.get("benchmark", "BTC/USDT"))
    tf = str(cfg.get("timeframe", "1d"))
    trend_period = int(cfg.get("trend_period", 200))
    try:
        bdf = provider.fetch_ohlcv(benchmark, timeframe=tf, limit=trend_period + 60)
    except Exception as exc:
        log.warning("Rejim benchmark verisi alınamadı (%s): %s", benchmark, exc)
        return RegimeAssessment(
            RegimeState.NEUTRAL, 0.0, "flat", float(cfg.get("neutral_ceiling", 0.6)),
            None, [f"Benchmark verisi yok ({benchmark}) → nötr"], ready=False,
        )

    base = assess_trend_regime(bdf, cfg)
    reasons = [f"Benchmark {benchmark} {tf}: {base.reasons[0]}", *base.reasons[1:]]
    score = base.score
    breadth_pct = None

    if bool(cfg.get("use_breadth", True)) and symbols:
        breadth_pct = compute_breadth(
            provider, symbols, timeframe=tf,
            ma_period=int(cfg.get("breadth_ma", 50)), limit=trend_period + 10,
        )
        if breadth_pct is not None:
            breadth_score = (breadth_pct - 50.0) / 50.0  # 0..100 → -1..+1
            w = float(cfg.get("breadth_weight", 0.4))
            score = round((1.0 - w) * base.score + w * breadth_score, 3)
            reasons.append(
                f"Breadth: {len(symbols)} sembolün %{breadth_pct}'i "
                f"{int(cfg.get('breadth_ma', 50))}-MA üstünde"
            )

    state, bias, ceiling = _score_to_state(score, cfg)
    return RegimeAssessment(state, score, bias, ceiling, breadth_pct, reasons, ready=base.ready)


# --------------------------------------------------------------------------- #
# Sinyal kapılama (gating)
# --------------------------------------------------------------------------- #


def gate_signal(
    action: Action, confidence: float, assessment: RegimeAssessment | None, cfg: dict
) -> tuple[Action, float, str | None]:
    """Rejime göre (action, confidence) ayarla.

    Dönüş: (yeni_action, yeni_confidence, gerekçe|None). Gerekçe None ise değişiklik yok.
      - NEUTRAL veya hazır değil veya HOLD → dokunma.
      - Karşı-rejim (RISK_OFF'ta BUY / RISK_ON'da SELL):
          mode='soft'  → güveni `penalty` ile çarp.
          mode='gate'  → HOLD'a düşür.
      - Pro-rejim sinyaller olduğu gibi bırakılır (güveni şişirme → kalibrasyon bozulmaz).
    """
    if assessment is None or not assessment.ready or assessment.state == RegimeState.NEUTRAL:
        return action, confidence, None
    if action not in (Action.BUY, Action.SELL):
        return action, confidence, None

    counter = (action == Action.BUY and assessment.state == RegimeState.RISK_OFF) or (
        action == Action.SELL and assessment.state == RegimeState.RISK_ON
    )
    if not counter:
        return action, confidence, None

    mode = str(cfg.get("mode", "soft")).lower()
    if mode == "gate":
        reason = (
            f"Rejim {assessment.state.value} (skor {assessment.score:+.2f}): "
            f"karşı-yön {action.value} elendi → HOLD"
        )
        return Action.HOLD, confidence, reason

    penalty = float(cfg.get("penalty", 0.5))
    new_conf = round(max(0.0, min(1.0, confidence * penalty)), 2)
    reason = (
        f"Rejim {assessment.state.value} (skor {assessment.score:+.2f}): karşı-yön "
        f"{action.value} güveni %{confidence * 100:.0f}→%{new_conf * 100:.0f}"
    )
    return action, new_conf, reason


# --------------------------------------------------------------------------- #
# Rejim kaynağı fabrikaları (sarmalayıcı için)
# --------------------------------------------------------------------------- #


def static_regime_fn(
    assessment: RegimeAssessment | None,
) -> Callable[[pd.DataFrame], RegimeAssessment | None]:
    """Tüm pencerelere AYNI değerlendirmeyi döndüren kaynak (canlı analiz için)."""

    def _fn(_df: pd.DataFrame) -> RegimeAssessment | None:
        return assessment

    return _fn


def precompute_daily_regimes(benchmark_daily: pd.DataFrame, cfg: dict) -> pd.Series:
    """Her günlük bar için (o güne KADARKİ veriyle) rejim hesaplar.

    Dönüş: index = günlük bar zaman damgası, value = RegimeAssessment.
    Backtest'te look-ahead'siz arama için kullanılır.
    """
    out: dict = {}
    for i in range(len(benchmark_daily)):
        out[benchmark_daily.index[i]] = assess_trend_regime(benchmark_daily.iloc[: i + 1], cfg)
    return pd.Series(out)


def make_backtest_regime_fn(
    benchmark_daily: pd.DataFrame, cfg: dict
) -> Callable[[pd.DataFrame], RegimeAssessment | None]:
    """Backtest için rejim kaynağı: intraday pencerenin son zamanına göre,
    KAPANMIŞ son günlük barın rejimini döndürür (look-ahead yok).

    Günlük bar `b` zaman damgasıyla açılır ve `b + 1 gün`de kapanır; intraday `t`
    anında ancak `b + 1gün ≤ t` ise bilinebilir. Bu yüzden `t - 1gün` eşiğiyle ararız.
    """
    regimes = precompute_daily_regimes(benchmark_daily, cfg)
    idx = regimes.index
    one_day = pd.Timedelta(days=1)

    def _fn(window: pd.DataFrame) -> RegimeAssessment | None:
        if len(window) == 0 or len(idx) == 0:
            return None
        ts = window.index[-1]
        try:
            cutoff = ts - one_day
            pos = int(idx.searchsorted(cutoff, side="right")) - 1
        except TypeError:
            return None
        if pos < 0:
            return None
        return regimes.iloc[pos]

    return _fn

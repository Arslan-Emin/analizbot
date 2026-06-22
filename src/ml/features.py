"""ML özellik (feature) ve etiket (label) üretimi.

TÜM özellikler NEDENSEL'dir (yalnız o ana kadarki veriyle hesaplanır) → tahminde
geleceğe bakmaz. Etiket ise SADECE eğitimde kullanılır: gelecekteki getiriye göre
BUY/SELL/HOLD. Bu, look-ahead bias'ı önlemenin anahtarıdır.

NOT: Yeni özellikler listenin SONUNA eklenir (yeniden sıralama yok). Eski modeller
kendi `bundle["features"]` alt kümesiyle çalışmaya devam eder (geriye dönük uyum).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.core.indicators import (
    adx,
    atr,
    bearish_engulfing,
    bollinger,
    bullish_engulfing,
    cci,
    doji,
    ema,
    hammer,
    keltner,
    macd,
    mfi,
    obv,
    rsi,
    shooting_star,
    stoch_rsi,
    stochastic,
    supertrend,
    vwap,
    williams_r,
)

# Modelin gördüğü özellik kolonları (sıra eğitim ve tahminde AYNI olmalı).
FEATURE_COLUMNS = [
    # --- Çekirdek (v1) ---
    "rsi",
    "macd",
    "macd_hist",
    "ema_gap_pct",
    "atr_pct",
    "adx",
    "di_diff",
    "bb_pct",
    "obv_slope",
    "ret_1",
    "ret_3",
    "ret_8",
    "vol_change",
    # --- Genişletilmiş osilatörler / trend ---
    "stoch_k",
    "stoch_d",
    "stoch_rsi",
    "mfi",
    "cci",
    "williams_r",
    "supertrend_dir",
    "vwap_gap_pct",
    "keltner_pct",
    # --- Mum formasyonları (0/1) ---
    "pat_bull_engulf",
    "pat_bear_engulf",
    "pat_hammer",
    "pat_star",
    "pat_doji",
    # --- Nedensel zaman özellikleri (döngüsel) ---
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    # --- Türev pozisyonlanma (opsiyonel; df'te yoksa nötr 0.0) ---
    "funding_rate",
]


def build_features(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """OHLCV'den nedensel özellik matrisi üretir (FEATURE_COLUMNS sırasıyla)."""
    close = df["close"]
    ema_fast_n = int(params.get("ema_fast", 12))
    ema_slow_n = int(params.get("ema_slow", 26))

    ef = ema(close, ema_fast_n)
    es = ema(close, ema_slow_n)
    macd_line, _, macd_hist = macd(close, ema_fast_n, ema_slow_n, 9)
    adx_line, plus_di, minus_di = adx(df, int(params.get("adx_period", 14)))
    _, bb_upper, bb_lower = bollinger(
        close, int(params.get("bb_period", 20)), float(params.get("bb_std", 2.0))
    )
    bb_width = (bb_upper - bb_lower).replace(0, np.nan)
    obv_series = obv(close, df["volume"])

    stoch_k, stoch_d = stochastic(
        df,
        int(params.get("stoch_k", 14)),
        int(params.get("stoch_d", 3)),
        int(params.get("stoch_smooth", 3)),
    )
    srsi_k, _ = stoch_rsi(close, int(params.get("rsi_period", 14)))
    _, st_dir = supertrend(
        df,
        int(params.get("supertrend_period", 10)),
        float(params.get("supertrend_mult", 3.0)),
    )
    vwap_series = vwap(df, int(params.get("vwap_window", 20)))
    k_period = int(params.get("keltner_period", 20))
    _, kc_upper, kc_lower = keltner(df, k_period, float(params.get("keltner_mult", 2.0)))
    kc_width = (kc_upper - kc_lower).replace(0, np.nan)

    out = pd.DataFrame(index=df.index)
    out["rsi"] = rsi(close, int(params.get("rsi_period", 14)))
    out["macd"] = macd_line
    out["macd_hist"] = macd_hist
    out["ema_gap_pct"] = (ef - es) / close * 100.0          # trend (fiyata göre %)
    out["atr_pct"] = atr(df, int(params.get("atr_period", 14))) / close * 100.0  # oynaklık %
    out["adx"] = adx_line                                    # trend gücü
    out["di_diff"] = plus_di - minus_di                      # yön baskısı
    out["bb_pct"] = (close - bb_lower) / bb_width            # Bollinger içi konum (0=alt,1=üst)
    out["obv_slope"] = obv_series.diff(3)                    # hacim baskısı eğimi
    out["ret_1"] = close.pct_change(1)                       # son getiriler (momentum)
    out["ret_3"] = close.pct_change(3)
    out["ret_8"] = close.pct_change(8)
    out["vol_change"] = df["volume"].pct_change(1)           # hacim değişimi

    # Genişletilmiş osilatörler / trend
    out["stoch_k"] = stoch_k
    out["stoch_d"] = stoch_d
    out["stoch_rsi"] = srsi_k
    out["mfi"] = mfi(df, int(params.get("mfi_period", 14)))
    out["cci"] = cci(df, int(params.get("cci_period", 20)))
    out["williams_r"] = williams_r(df, int(params.get("williams_period", 14)))
    out["supertrend_dir"] = st_dir                           # +1 yukarı / -1 aşağı
    out["vwap_gap_pct"] = (close - vwap_series) / close * 100.0   # VWAP'a göre konum %
    out["keltner_pct"] = (close - kc_lower) / kc_width       # Keltner içi konum

    # Mum formasyonları → 0/1
    out["pat_bull_engulf"] = bullish_engulfing(df).astype(float)
    out["pat_bear_engulf"] = bearish_engulfing(df).astype(float)
    out["pat_hammer"] = hammer(df).astype(float)
    out["pat_star"] = shooting_star(df).astype(float)
    out["pat_doji"] = doji(df).astype(float)

    # Nedensel zaman özellikleri (döngüsel sin/cos). DatetimeIndex değilse nötr 0.
    out = _add_time_features(out, df.index)

    # Türev pozisyonlanma (opsiyonel): df'te `funding_rate` varsa kullan (look-ahead'siz
    # hizalanmış, bkz. derivatives.merge_funding_history), yoksa nötr 0.0. Bu sayede eski
    # modeller ve funding'siz eğitim sorunsuz çalışır (geriye/ileriye uyum).
    out["funding_rate"] = (
        df["funding_rate"].astype(float) if "funding_rate" in df.columns else 0.0
    )

    return out[FEATURE_COLUMNS]


def _add_time_features(out: pd.DataFrame, index) -> pd.DataFrame:
    """Saat ve haftanın günü için döngüsel sin/cos özellikleri (nedensel)."""
    if isinstance(index, pd.DatetimeIndex):
        hour = index.hour.to_numpy(dtype=float)
        dow = index.dayofweek.to_numpy(dtype=float)
        out["hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
        out["hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
        out["dow_sin"] = np.sin(2 * np.pi * dow / 7.0)
        out["dow_cos"] = np.cos(2 * np.pi * dow / 7.0)
    else:
        out["hour_sin"] = 0.0
        out["hour_cos"] = 0.0
        out["dow_sin"] = 0.0
        out["dow_cos"] = 0.0
    return out


def build_labels(df: pd.DataFrame, horizon: int, threshold_pct: float) -> pd.Series:
    """Eğitim etiketi: `horizon` bar SONRAKİ getiri eşiği aşarsa BUY/SELL, yoksa HOLD.

    Son `horizon` barda gelecek bilinmediği için etiket NaN olur (eğitimden düşülür).
    """
    close = df["close"]
    forward_return = close.shift(-horizon) / close - 1.0
    th = threshold_pct / 100.0

    label = pd.Series(
        np.where(forward_return > th, "BUY", np.where(forward_return < -th, "SELL", "HOLD")),
        index=df.index,
        dtype=object,
    )
    label[forward_return.isna()] = np.nan  # son horizon bar: hedef yok
    return label

"""Teknik indikatörler — saf pandas/numpy ile el yazımı.

Neden kütüphane değil? 6 indikatör için tam deterministik, test edilebilir
ve okunabilir kod istiyoruz (pandas-ta gibi paketler bakımsız/kırılgan).
RSI ve ATR'de **Wilder yumuşatması** (RMA) kullanılır — TradingView ve çoğu
platformun standardı budur (basit hareketli ortalama DEĞİL).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    """Üssel hareketli ortalama (EMA).

    `adjust=False`: her yeni bar bir öncekini sabit ağırlıkla günceller
    (yinelemeli/recursive form) — canlı ve geçmiş hesap aynı sonucu verir.
    """
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Göreli Güç Endeksi (Wilder RSI).

    Adımlar: fiyat farkını kazanç/kayıp olarak ayır → her birini Wilder
    yumuşatmasıyla (alpha = 1/period) ortala → RS = avgGain/avgLoss →
    RSI = 100 - 100/(1+RS). Sonuç 0-100 aralığındadır.
    """
    delta = series.diff()
    gain = delta.clip(lower=0.0)        # negatifleri 0 yap → sadece kazançlar
    loss = -delta.clip(upper=0.0)       # pozitifleri 0 yap, işaret çevir → sadece kayıplar

    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

    # Sıfıra bölme uyarılarını sustur (hiç kayıp yoksa RS sonsuz olur → RSI 100).
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
    rsi_val = 100.0 - (100.0 / (1.0 + rs))
    # Kenar durumlar (kayıp yokken bölme tanımsız):
    #  - sadece kazanç var (avg_loss=0, avg_gain>0) → RSI 100
    #  - tamamen düz, hiç hareket yok (ikisi de 0)   → RSI 50 (nötr, 100 DEĞİL)
    rsi_val = rsi_val.mask((avg_loss == 0.0) & (avg_gain > 0.0), 100.0)
    rsi_val = rsi_val.mask((avg_loss == 0.0) & (avg_gain == 0.0), 50.0)
    return rsi_val


def macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD = hızlı EMA - yavaş EMA; sinyal = MACD'nin EMA'sı; histogram = MACD - sinyal."""
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Ortalama Gerçek Aralık (Wilder ATR) — oynaklık ölçüsü.

    Gerçek Aralık (TR) = max(high-low, |high-önceki_close|, |low-önceki_close|).
    Sonra TR Wilder yumuşatmasıyla ortalanır. Stop/TP mesafeleri buradan gelir.
    """
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)

    true_range = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return true_range.ewm(alpha=1 / period, adjust=False).mean()


def crossover(a: pd.Series, b: pd.Series) -> bool:
    """`a`, `b`'yi yukarı kesti mi? (önceki barda a<=b, son barda a>b)."""
    if len(a) < 2 or len(b) < 2:
        return False
    return bool(a.iloc[-2] <= b.iloc[-2] and a.iloc[-1] > b.iloc[-1])


def crossunder(a: pd.Series, b: pd.Series) -> bool:
    """`a`, `b`'yi aşağı kesti mi? (önceki barda a>=b, son barda a<b)."""
    if len(a) < 2 or len(b) < 2:
        return False
    return bool(a.iloc[-2] >= b.iloc[-2] and a.iloc[-1] < b.iloc[-1])


def adx(
    df: pd.DataFrame, period: int = 14
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """ADX + yönlü göstergeler (+DI/-DI) — Wilder.

    ADX trendin GÜCÜNÜ ölçer (yönünü değil): >25 güçlü trend, <20 yatay.
    +DI > -DI yukarı baskı, tersi aşağı baskı. Confluence stratejisi bunu
    "trend gücü filtresi" olarak kullanır.
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]

    up_move = high.diff()
    down_move = -low.diff()
    # Yönlü hareket: yalnız baskın ve pozitif yöndeki hareketi say.
    plus_dm = ((up_move > down_move) & (up_move > 0)) * up_move.fillna(0.0)
    minus_dm = ((down_move > up_move) & (down_move > 0)) * down_move.fillna(0.0)

    prev_close = close.shift(1)
    true_range = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr_w = true_range.ewm(alpha=1 / period, adjust=False).mean()

    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100.0 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_w
        minus_di = 100.0 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_w
        dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    dx = dx.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    adx_line = dx.ewm(alpha=1 / period, adjust=False).mean()
    return adx_line, plus_di.fillna(0.0), minus_di.fillna(0.0)


def bollinger(
    series: pd.Series, period: int = 20, num_std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bantları: orta (SMA), üst, alt. Oynaklık/aşırılık bağlamı verir."""
    mid = series.rolling(period).mean()
    std = series.rolling(period).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    return mid, upper, lower


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume: hacmi fiyat yönüyle biriktirir (alıcı/satıcı baskısı)."""
    direction = np.sign(close.diff()).fillna(0.0)
    return (direction * volume).cumsum()


# ---------------------------------------------------------------------------
# Ek indikatörler (öğrenme/analiz genişletmesi). Hepsi nedensel ve deterministik;
# kenar durumlar (sıfıra bölme, ısınma) nötr değerlere düşürülür.
# ---------------------------------------------------------------------------


def stochastic(
    df: pd.DataFrame, k_period: int = 14, d_period: int = 3, smooth_k: int = 3
) -> tuple[pd.Series, pd.Series]:
    """Stochastic Oscillator (yavaş): %K ve %D, 0-100 arası.

    %K = 100*(close - en_düşük) / (en_yüksek - en_düşük), `smooth_k` ile yumuşatılır;
    %D = %K'nın `d_period` SMA'sı. Aralık sıfırsa (düz piyasa) nötr 50 verilir.
    """
    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    rng = (high_max - low_min).replace(0.0, np.nan)
    raw_k = 100.0 * (df["close"] - low_min) / rng
    raw_k = raw_k.fillna(50.0)
    k = raw_k.rolling(smooth_k).mean()
    d = k.rolling(d_period).mean()
    return k, d


def stoch_rsi(
    close: pd.Series,
    rsi_period: int = 14,
    stoch_period: int = 14,
    k_smooth: int = 3,
    d_smooth: int = 3,
) -> tuple[pd.Series, pd.Series]:
    """Stochastic RSI: RSI'a stochastic uygulayarak aşırılığı keskinleştirir (0-100).

    RSI hesaplanır → son `stoch_period` içindeki min-max'e göre normalize edilir →
    %K ve %D yumuşatılır. RSI aralığı sıfırsa nötr 50 verilir.
    """
    r = rsi(close, rsi_period)
    r_min = r.rolling(stoch_period).min()
    r_max = r.rolling(stoch_period).max()
    rng = (r_max - r_min).replace(0.0, np.nan)
    stochrsi = ((r - r_min) / rng).fillna(0.5) * 100.0
    k = stochrsi.rolling(k_smooth).mean()
    d = k.rolling(d_smooth).mean()
    return k, d


def mfi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Money Flow Index — hacim-ağırlıklı RSI (0-100).

    Tipik fiyat (H+L+C)/3 × hacim = ham para akışı. Tipik fiyat yükseldiyse
    pozitif, düştüyse negatif akış sayılır; oranlanıp RSI formuna sokulur.
    Negatif akış yoksa 100, hiç akış yoksa nötr 50.
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    raw_flow = typical * df["volume"]
    delta = typical.diff()
    pos_flow = raw_flow.where(delta > 0, 0.0).rolling(period).sum()
    neg_flow = raw_flow.where(delta < 0, 0.0).rolling(period).sum()

    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = pos_flow / neg_flow
    mfi_val = 100.0 - (100.0 / (1.0 + ratio))
    mfi_val = mfi_val.mask((neg_flow == 0.0) & (pos_flow > 0.0), 100.0)
    mfi_val = mfi_val.mask((neg_flow == 0.0) & (pos_flow == 0.0), 50.0)
    return mfi_val


def cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Commodity Channel Index: tipik fiyatın ortalamadan sapması (ortalama ±100).

    CCI = (tipik - SMA(tipik)) / (0.015 × ortalama_mutlak_sapma).
    Sapma sıfırsa (düz) 0 verilir.
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    sma = typical.rolling(period).mean()
    mean_dev = (typical - sma).abs().rolling(period).mean().replace(0.0, np.nan)
    cci_val = (typical - sma) / (0.015 * mean_dev)
    return cci_val.fillna(0.0)


def williams_r(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Williams %R: stochastic'in tersi, [-100, 0]. -20 üstü aşırı alım, -80 altı aşırı satım."""
    high_max = df["high"].rolling(period).max()
    low_min = df["low"].rolling(period).min()
    rng = (high_max - low_min).replace(0.0, np.nan)
    wr = -100.0 * (high_max - df["close"]) / rng
    return wr.fillna(-50.0)


def supertrend(
    df: pd.DataFrame, period: int = 10, multiplier: float = 3.0
) -> tuple[pd.Series, pd.Series]:
    """Supertrend: ATR-bantlı trend takip çizgisi + yön (+1 yukarı / -1 aşağı).

    hl2 ± multiplier*ATR ile bantlar kurulur; bantlar yinelemeli (recursive)
    daraltılır ve fiyat bandı kırınca yön döner. Yinelemeli olduğu için saf
    döngüyle hesaplanır (deterministik). ATR ısınana dek yön 0/NaN olabilir.
    """
    atr_val = atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2.0
    upper = hl2 + multiplier * atr_val
    lower = hl2 - multiplier * atr_val

    close = df["close"].to_numpy()
    up = upper.to_numpy()
    lo = lower.to_numpy()
    n = len(df)
    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    st = np.full(n, np.nan)
    direction = np.zeros(n)

    for i in range(n):
        if i == 0 or np.isnan(up[i]) or np.isnan(lo[i]):
            final_upper[i] = up[i]
            final_lower[i] = lo[i]
            st[i] = up[i]
            direction[i] = -1.0
            continue
        # Bantları daralt: yeni bant öncekinden daha sıkıysa veya fiyat aştıysa güncelle.
        final_upper[i] = (
            up[i] if (up[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1])
            else final_upper[i - 1]
        )
        final_lower[i] = (
            lo[i] if (lo[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1])
            else final_lower[i - 1]
        )
        # Önceki supertrend hangi banttaysa ona göre yön/çizgi belirle.
        if st[i - 1] == final_upper[i - 1]:
            st[i] = final_upper[i] if close[i] <= final_upper[i] else final_lower[i]
        else:
            st[i] = final_lower[i] if close[i] >= final_lower[i] else final_upper[i]
        direction[i] = 1.0 if close[i] > st[i] else -1.0

    index = df.index
    return pd.Series(st, index=index), pd.Series(direction, index=index)


def vwap(df: pd.DataFrame, window: int | None = 20) -> pd.Series:
    """Hacim-ağırlıklı ortalama fiyat (VWAP).

    `window` verilirse kayan (rolling) VWAP; None ise kümülatif. Kripto'da seans
    sıfırlaması olmadığından kayan pencere varsayılandır. Hacim sıfırsa NaN.
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = typical * df["volume"]
    if window is None:
        return pv.cumsum() / df["volume"].cumsum().replace(0.0, np.nan)
    vol_sum = df["volume"].rolling(window).sum().replace(0.0, np.nan)
    return pv.rolling(window).sum() / vol_sum


def keltner(
    df: pd.DataFrame, period: int = 20, multiplier: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Keltner Kanalları: orta = EMA, üst/alt = EMA ± multiplier*ATR (oynaklık zarfı)."""
    mid = ema(df["close"], period)
    atr_val = atr(df, period)
    upper = mid + multiplier * atr_val
    lower = mid - multiplier * atr_val
    return mid, upper, lower


# --- Mum (candlestick) formasyonları: hepsi son bara göre boolean seri döndürür ---


def _candle_parts(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Gövde, toplam menzil, üst ve alt fitil uzunluklarını döndürür."""
    body = (df["close"] - df["open"]).abs()
    rng = (df["high"] - df["low"]).replace(0.0, np.nan)
    upper_shadow = df["high"] - df[["open", "close"]].max(axis=1)
    lower_shadow = df[["open", "close"]].min(axis=1) - df["low"]
    return body, rng, upper_shadow, lower_shadow


def doji(df: pd.DataFrame, body_frac: float = 0.1) -> pd.Series:
    """Doji: gövde, menzilin çok küçük bir kısmı (kararsızlık)."""
    body, rng, _, _ = _candle_parts(df)
    return (body <= body_frac * rng).fillna(False)


def hammer(df: pd.DataFrame) -> pd.Series:
    """Çekiç: küçük gövde üstte, uzun alt fitil (dip dönüş adayı)."""
    body, rng, upper_shadow, lower_shadow = _candle_parts(df)
    return (
        (lower_shadow >= 2.0 * body) & (upper_shadow <= 0.3 * rng) & (body > 0.0)
    ).fillna(False)


def shooting_star(df: pd.DataFrame) -> pd.Series:
    """Kayan yıldız: küçük gövde altta, uzun üst fitil (tepe dönüş adayı)."""
    body, rng, upper_shadow, lower_shadow = _candle_parts(df)
    return (
        (upper_shadow >= 2.0 * body) & (lower_shadow <= 0.3 * rng) & (body > 0.0)
    ).fillna(False)


def bullish_engulfing(df: pd.DataFrame) -> pd.Series:
    """Boğa yutan: önceki ayı mumunu saran daha büyük boğa mumu."""
    o, c = df["open"], df["close"]
    prev_o, prev_c = o.shift(1), c.shift(1)
    return ((prev_c < prev_o) & (c > o) & (o <= prev_c) & (c >= prev_o)).fillna(False)


def bearish_engulfing(df: pd.DataFrame) -> pd.Series:
    """Ayı yutan: önceki boğa mumunu saran daha büyük ayı mumu."""
    o, c = df["open"], df["close"]
    prev_o, prev_c = o.shift(1), c.shift(1)
    return ((prev_c > prev_o) & (c < o) & (o >= prev_c) & (c <= prev_o)).fillna(False)


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """OHLCV'yi daha ÜST bir zaman dilimine toplar (örn '1h' -> '4h').

    Çok-zaman-dilimli (MTF) analiz için: üst TF trendini AYNI veriden türetiriz,
    böylece ekstra ağ isteği gerekmez ve backtest'te de deterministik kalır.
    """
    agg = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    # Yalnız mevcut kolonları topla (fazladan indikatör kolonu varsa görmezden gel).
    cols = {k: v for k, v in agg.items() if k in df.columns}
    return df.resample(rule).agg(cols).dropna()


def compute_indicators(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Ham OHLCV'ye 6 indikatör kolonu ekleyip yeni DataFrame döndürür.

    Girdi kolonları: open, high, low, close, volume.
    Eklenen kolonlar: ema_fast, ema_slow, rsi, macd, macd_signal, atr (+macd_hist).
    Orijinal df değiştirilmez (kopya üzerinde çalışılır).
    """
    out = df.copy()
    ema_fast_n = int(params.get("ema_fast", 12))
    ema_slow_n = int(params.get("ema_slow", 26))
    rsi_n = int(params.get("rsi_period", 14))
    atr_n = int(params.get("atr_period", 14))
    macd_signal_n = int(params.get("macd_signal", 9))

    out["ema_fast"] = ema(out["close"], ema_fast_n)
    out["ema_slow"] = ema(out["close"], ema_slow_n)
    out["rsi"] = rsi(out["close"], rsi_n)
    macd_line, signal_line, histogram = macd(out["close"], ema_fast_n, ema_slow_n, macd_signal_n)
    out["macd"] = macd_line
    out["macd_signal"] = signal_line
    out["macd_hist"] = histogram
    out["atr"] = atr(out, atr_n)

    # Opt-in genişletilmiş indikatörler (varsayılan KAPALI → mevcut davranış aynı).
    # `extended_indicators: true` veya tek tek bayraklarla açılır; screen gibi sıcak
    # yollarda gereksiz hesaplamayı önlemek için kapı koyulur.
    if params.get("extended_indicators"):
        k, d = stochastic(
            out,
            int(params.get("stoch_k", 14)),
            int(params.get("stoch_d", 3)),
            int(params.get("stoch_smooth", 3)),
        )
        out["stoch_k"] = k
        out["stoch_d"] = d
        out["mfi"] = mfi(out, int(params.get("mfi_period", 14)))
        out["cci"] = cci(out, int(params.get("cci_period", 20)))
        out["williams_r"] = williams_r(out, int(params.get("williams_period", 14)))
        st_line, st_dir = supertrend(
            out,
            int(params.get("supertrend_period", 10)),
            float(params.get("supertrend_mult", 3.0)),
        )
        out["supertrend"] = st_line
        out["supertrend_dir"] = st_dir
    return out

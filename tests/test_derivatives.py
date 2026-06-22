"""Türev (funding/OI) verisi testleri — ağsız, deterministik, look-ahead kontrollü."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.core.derivatives import (
    align_history_to_index,
    derivatives_snapshot,
    funding_sentiment,
    merge_funding_history,
    oi_trend,
    to_perp_symbol,
)
from src.ml.features import build_features

# ------------------------------ perp eşleme -------------------------------- #


def test_to_perp_symbol():
    assert to_perp_symbol("BTC/USDT") == "BTC/USDT:USDT"
    assert to_perp_symbol("ETH/USDT:USDT") == "ETH/USDT:USDT"  # zaten perp
    assert to_perp_symbol("WEIRD") == "WEIRD"  # bozuk → dokunma


# --------------------------- funding sentiment ----------------------------- #


def test_funding_sentiment_levels():
    assert funding_sentiment(0.001)[0] == "AŞIRI_LONG"
    assert funding_sentiment(0.0004)[0] == "LONG_AĞIRLIKLI"
    assert funding_sentiment(0.0001)[0] == "NÖTR"
    assert funding_sentiment(-0.0004)[0] == "SHORT_AĞIRLIKLI"
    assert funding_sentiment(-0.001)[0] == "AŞIRI_SHORT"


# ------------------------------- OI eğilimi -------------------------------- #


def test_oi_trend():
    assert oi_trend([900, 950, 1000])[0] == "ARTIYOR"
    assert oi_trend([1000, 950, 900])[0] == "AZALIYOR"
    assert oi_trend([1000, 1001, 999])[0] == "YATAY"
    assert oi_trend([1000])[0] == "BİLİNMİYOR"


# ------------------------ hizalama (look-ahead yok) ------------------------ #


def test_align_history_ffill_no_lookahead():
    idx = pd.to_datetime(["2024-01-01 00:00", "2024-01-01 08:00"], utc=True)
    hist = pd.Series([0.1, 0.2], index=idx)
    target = pd.date_range("2024-01-01 00:00", periods=13, freq="h", tz="UTC")
    aligned = align_history_to_index(hist, target)
    # 00:00-07:00 → ilk değer (0.1); 08:00 sonrası → 0.2 (geleceği sızdırmaz)
    assert aligned.loc["2024-01-01 00:00"] == 0.1
    assert aligned.loc["2024-01-01 07:00"] == 0.1
    assert aligned.loc["2024-01-01 08:00"] == 0.2
    assert aligned.loc["2024-01-01 12:00"] == 0.2


# ----------------------------- merge funding ------------------------------- #


class _FundingProvider:
    def fetch_funding_rate_history(self, perp, since_ms=None, limit=1000):
        base = pd.Timestamp("2024-01-01", tz="UTC")
        return [
            {"timestamp": int((base + pd.Timedelta(hours=8 * i)).timestamp() * 1000),
             "fundingRate": 0.0001 * (i + 1)}
            for i in range(5)
        ]


def _flat_df(n: int) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx
    )


def test_merge_funding_history_adds_column():
    out = merge_funding_history(_flat_df(40), _FundingProvider(), "BTC/USDT", {})
    assert "funding_rate" in out.columns
    assert out["funding_rate"].notna().all()  # fillna(0.0) → NaN yok
    assert out["funding_rate"].iloc[0] == 0.0001  # ilk settlement değeri


def test_merge_funding_history_graceful_without_support():
    out = merge_funding_history(_flat_df(5), object(), "BTC/USDT", {})  # metot yok
    assert "funding_rate" not in out.columns  # df değişmedi


# --------------------------- snapshot (canlı) ------------------------------ #


class _DerivProvider:
    def fetch_funding_rate(self, perp):
        return {"fundingRate": 0.0008}

    def fetch_open_interest(self, perp):
        return {"openInterestAmount": 1000.0}

    def fetch_open_interest_history(self, perp, timeframe="8h", limit=30):
        return [{"openInterestAmount": v} for v in [900, 950, 1000]]


def test_derivatives_snapshot_full():
    snap = derivatives_snapshot(_DerivProvider(), "BTC/USDT", {})
    assert snap["perp_symbol"] == "BTC/USDT:USDT"
    assert snap["funding_rate"] == 0.0008
    assert snap["funding_sentiment"][0] == "AŞIRI_LONG"
    assert snap["open_interest"] == 1000.0
    assert snap["oi_trend"][0] == "ARTIYOR"


def test_derivatives_snapshot_graceful_no_support():
    snap = derivatives_snapshot(object(), "BTC/USDT", {})  # hiç metot yok
    assert snap == {"perp_symbol": "BTC/USDT:USDT"}  # çökme yok, kısmi


# --------------------- build_features funding entegrasyonu ----------------- #


def _ohlcv(n: int = 60) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = pd.Series(np.linspace(100, 110, n), index=idx)
    return pd.DataFrame(
        {"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1000.0}
    )


def test_build_features_funding_from_df():
    df = _ohlcv()
    df["funding_rate"] = 0.0005
    feats = build_features(df, {})
    assert "funding_rate" in feats.columns
    assert (feats["funding_rate"] == 0.0005).all()


def test_build_features_funding_default_zero():
    feats = build_features(_ohlcv(), {})
    assert "funding_rate" in feats.columns
    assert (feats["funding_rate"] == 0.0).all()  # df'te yoksa nötr

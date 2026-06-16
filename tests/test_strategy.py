"""EmaRsiStrategy birim testleri — ağsız, kurgulanmış fiyat serileriyle deterministik."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.core.models import Action
from src.strategies.ema_rsi import EmaRsiStrategy


def _df_from_closes(closes: np.ndarray) -> pd.DataFrame:
    """Kapanış dizisinden makul bir OHLCV DataFrame kurar (testler için yardımcı)."""
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="h", tz="UTC")
    close = pd.Series(closes, index=idx, dtype=float)
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.5
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.5
    volume = pd.Series(1000.0, index=idx)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}
    )


def test_uptrend_produces_buy(ema_rsi_params):
    # Önce düşüş, sonra düzenli geri çekilmeli yükseliş → güçlü ama aşırı-alım
    # olmayan yukarı trend (RSI ~60).
    down = np.linspace(100, 88, 30)
    up = 88 + np.cumsum(np.array([1.0, -0.6] * 25))
    df = _df_from_closes(np.concatenate([down, up]))

    sig = EmaRsiStrategy(ema_rsi_params).generate(df, "TEST/USDT")

    assert sig.action == Action.BUY
    assert sig.reasons  # gerekçe boş olmamalı
    assert 0.0 <= sig.confidence <= 1.0
    # BUY'da stop girişin altında, hedef üstünde olmalı
    assert sig.stop_loss < sig.suggested_entry < sig.take_profit


def test_downtrend_produces_sell(ema_rsi_params):
    # İvmelenen (giderek dikleşen) aşağı trend → MACD ayıda kalır → SELL.
    closes = 100.0 - 0.008 * np.arange(60) ** 2
    df = _df_from_closes(closes)

    sig = EmaRsiStrategy(ema_rsi_params).generate(df, "TEST/USDT")

    assert sig.action == Action.SELL
    assert sig.reasons
    # SELL'de stop girişin üstünde, hedef altında olmalı
    assert sig.take_profit < sig.suggested_entry < sig.stop_loss


def test_flat_market_produces_hold(ema_rsi_params):
    # Net yön olmayan, çok küçük genlikli yatay piyasa → HOLD.
    closes = 100 + 0.1 * np.where(np.arange(80) % 2 == 0, 1.0, -1.0)
    df = _df_from_closes(closes)

    sig = EmaRsiStrategy(ema_rsi_params).generate(df, "TEST/USDT")

    assert sig.action == Action.HOLD
    assert sig.stop_loss is None
    assert sig.take_profit is None


def test_strategy_is_deterministic(ema_rsi_params):
    # Aynı girdi → aynı çıktı (created_at hariç). Backtest ve testler buna dayanır.
    down = np.linspace(100, 88, 30)
    up = 88 + np.cumsum(np.array([1.0, -0.6] * 25))
    df = _df_from_closes(np.concatenate([down, up]))

    s1 = EmaRsiStrategy(ema_rsi_params).generate(df, "TEST/USDT")
    s2 = EmaRsiStrategy(ema_rsi_params).generate(df, "TEST/USDT")

    assert (s1.action, s1.confidence, s1.price, s1.reasons) == (
        s2.action,
        s2.confidence,
        s2.price,
        s2.reasons,
    )
    assert (s1.suggested_entry, s1.stop_loss, s1.take_profit) == (
        s2.suggested_entry,
        s2.stop_loss,
        s2.take_profit,
    )

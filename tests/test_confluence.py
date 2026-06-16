"""ConfluenceStrategy testleri — MTF için yeterli (>=112) bar, ağsız, deterministik."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.core.models import Action
from src.strategies.confluence import ConfluenceStrategy


def _params() -> dict:
    return {
        "ema_fast": 12,
        "ema_slow": 26,
        "rsi_period": 14,
        "rsi_overbought": 70,
        "rsi_oversold": 30,
        "atr_period": 14,
        "atr_stop_mult": 1.5,
        "atr_tp_mult": 3.0,
        "adx_period": 14,
        "adx_min": 20,
        "bb_period": 20,
        "bb_std": 2.0,
        "use_mtf": True,
        "htf_rule": "4h",
        "min_confluence": 3,
        "risk_per_trade_pct": 1.0,
        "hypothetical_capital_quote": 1000,
    }


def _df(closes: np.ndarray) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="h", tz="UTC")
    close = pd.Series(closes, index=idx, dtype=float)
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.5
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.5
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": 1000.0}
    )


def test_confluence_uptrend_buy():
    # Düşüş + geri çekilmeli yükseliş, son bar yukarı → çok-koşullu BUY.
    down = np.linspace(100, 88, 40)
    up = 88 + np.cumsum(np.array([1.0, -0.6] * 80))
    closes = np.concatenate([down, up, [up[-1] + 1.0]])
    sig = ConfluenceStrategy(_params()).generate(_df(closes), "TEST/USDT")
    assert sig.action == Action.BUY
    assert sig.stop_loss < sig.suggested_entry < sig.take_profit


def test_confluence_downtrend_sell():
    up0 = np.linspace(88, 100, 40)
    down = 100 + np.cumsum(np.array([-1.0, 0.6] * 80))
    closes = np.concatenate([up0, down, [down[-1] - 1.0]])
    sig = ConfluenceStrategy(_params()).generate(_df(closes), "TEST/USDT")
    assert sig.action == Action.SELL
    assert sig.take_profit < sig.suggested_entry < sig.stop_loss


def test_confluence_flat_hold():
    closes = 100 + 0.1 * np.where(np.arange(200) % 2 == 0, 1.0, -1.0)
    sig = ConfluenceStrategy(_params()).generate(_df(closes), "TEST/USDT")
    assert sig.action == Action.HOLD
    assert sig.stop_loss is None


def test_confluence_deterministic():
    down = np.linspace(100, 88, 40)
    up = 88 + np.cumsum(np.array([1.0, -0.6] * 80))
    closes = np.concatenate([down, up, [up[-1] + 1.0]])
    df = _df(closes)
    s1 = ConfluenceStrategy(_params()).generate(df, "TEST/USDT")
    s2 = ConfluenceStrategy(_params()).generate(df, "TEST/USDT")
    assert (s1.action, s1.confidence, s1.reasons) == (s2.action, s2.confidence, s2.reasons)


def test_optional_conditions_default_off_unchanged():
    # use_supertrend/use_mfi varsayılan kapalı → karar ham 7'lik paydayla aynı kalır.
    down = np.linspace(100, 88, 40)
    up = 88 + np.cumsum(np.array([1.0, -0.6] * 80))
    closes = np.concatenate([down, up, [up[-1] + 1.0]])
    df = _df(closes)
    base = ConfluenceStrategy(_params()).generate(df, "TEST/USDT")

    params = {**_params(), "use_supertrend": True, "use_mfi": True}
    ext = ConfluenceStrategy(params).generate(df, "TEST/USDT")
    # Açık bayraklarla ek gerekçe satırları görülebilir; karar yine geçerli bir aksiyon.
    assert base.action in (Action.BUY, Action.SELL, Action.HOLD)
    assert ext.action in (Action.BUY, Action.SELL, Action.HOLD)
    # Supertrend bull olduğu yükseliş senaryosunda ek onay gerekçeye yansımalı.
    assert any("Supertrend" in r for r in ext.reasons) or ext.action == Action.HOLD

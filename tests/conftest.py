"""Testler için ortak fixture'lar (pytest otomatik bulur)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "ohlcv_btcusdt.csv"


@pytest.fixture
def ohlcv() -> pd.DataFrame:
    """Sabit sentetik OHLCV (timestamp UTC index). Ağ yok, deterministik."""
    df = pd.read_csv(FIXTURE_CSV, parse_dates=["timestamp"])
    return df.set_index("timestamp")


@pytest.fixture
def ema_rsi_params() -> dict:
    """config.yaml'daki EmaRsiStrategy varsayılanları (testler için sabit kopya)."""
    return {
        "ema_fast": 12,
        "ema_slow": 26,
        "rsi_period": 14,
        "rsi_overbought": 70,
        "rsi_oversold": 30,
        "atr_period": 14,
        "atr_stop_mult": 1.5,
        "atr_tp_mult": 3.0,
        "risk_per_trade_pct": 1.0,
        "hypothetical_capital_quote": 1000,
    }

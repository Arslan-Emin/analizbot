"""YFinanceData + market_registry testleri — saf kısımlar, ağsız."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.data.crypto_ccxt import CcxtBinanceData
from src.data.market_registry import _looks_like_us_equity, get_provider
from src.data.yfinance_data import DEFAULT_UNIVERSE, YFinanceData

# ------------------------------ interval/period --------------------------- #


def test_interval_mapping():
    assert YFinanceData._interval("1d") == "1d"
    assert YFinanceData._interval("1h") == "1h"
    with pytest.raises(ValueError, match="desteklemiyor"):
        YFinanceData._interval("4h")  # yfinance 4h vermez


def test_period_for():
    assert YFinanceData._period_for("1d") == "max"
    assert YFinanceData._period_for("1h") == "730d"


# --------------------------------- normalize ------------------------------- #


def test_normalize_multiindex_columns():
    idx = pd.date_range("2024-01-01", periods=5, freq="D")  # tz-naive (günlük)
    cols = pd.MultiIndex.from_product(
        [["Open", "High", "Low", "Close", "Volume"], ["AAPL"]]
    )
    raw = pd.DataFrame(np.arange(25).reshape(5, 5).astype(float), index=idx, columns=cols)
    norm = YFinanceData._normalize(raw)
    assert list(norm.columns) == ["open", "high", "low", "close", "volume"]
    assert str(norm.index.tz) == "UTC"
    assert len(norm) == 5


def test_normalize_simple_columns():
    idx = pd.date_range("2024-01-01", periods=3, freq="D", tz="US/Eastern")
    raw = pd.DataFrame(
        {"Open": [1, 2, 3], "High": [2, 3, 4], "Low": [0, 1, 2], "Close": [1.5, 2.5, 3.5],
         "Volume": [10, 20, 30]},
        index=idx,
    )
    norm = YFinanceData._normalize(raw)
    assert list(norm.columns) == ["open", "high", "low", "close", "volume"]
    assert str(norm.index.tz) == "UTC"  # ET → UTC çevrildi


def test_normalize_empty():
    norm = YFinanceData._normalize(pd.DataFrame())
    assert list(norm.columns) == ["open", "high", "low", "close", "volume"]
    assert norm.empty


def test_list_symbols_default_universe():
    prov = YFinanceData()
    syms = prov.list_symbols()
    assert "AAPL" in syms
    assert syms == DEFAULT_UNIVERSE


def test_is_market_open_returns_bool():
    assert isinstance(YFinanceData().is_market_open(), bool)


# ----------------------------- registry routing ---------------------------- #


def test_looks_like_us_equity():
    assert _looks_like_us_equity("AAPL")
    assert _looks_like_us_equity("BRK-B")
    assert _looks_like_us_equity("^VIX")  # endeks
    assert not _looks_like_us_equity("BTC/USDT")  # crypto pair
    assert not _looks_like_us_equity("ETH/USDT")


def test_get_provider_routes_by_symbol():
    assert isinstance(get_provider("AAPL"), YFinanceData)
    assert isinstance(get_provider("SPY"), YFinanceData)
    assert isinstance(get_provider("BTC/USDT"), CcxtBinanceData)

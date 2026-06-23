"""Executor fabrikası testleri — ÜÇLÜ KİLİT güvenlik kapısı (ağsız)."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from src.data.base import MarketDataProvider
from src.execution.factory import LiveLockError, build_executor
from src.execution.paper import PaperExecutor
from src.storage.db import Repository


class _PriceProvider(MarketDataProvider):
    name = "mock"
    market = "crypto"

    def fetch_ohlcv(self, symbol, timeframe="1h", limit=500):
        return pd.DataFrame()

    def get_ticker(self, symbol):
        return 100.0

    def list_symbols(self):
        return ["BTC/USDT"]


def _cfg(yaml: dict, **settings):
    base = dict(
        live_trading=False, binance_api_key=None, binance_api_secret=None,
        binance_testnet_api_key=None, binance_testnet_api_secret=None,
    )
    base.update(settings)
    return SimpleNamespace(settings=SimpleNamespace(**base), yaml=yaml)


def _repo(tmp_path):
    return Repository(f"sqlite:///{(tmp_path / 'fac.db').as_posix()}")


def test_paper_is_default(tmp_path):
    cfg = _cfg({"execution": {"mode": "paper", "allocation_quote_cap": 0}})
    ex = build_executor(cfg, _PriceProvider(), _repo(tmp_path))
    assert isinstance(ex, PaperExecutor)
    assert ex.paper_capital == 1000.0  # cap 0 → varsayılan


def test_live_rejected_without_any_lock(tmp_path):
    cfg = _cfg({"execution": {"mode": "live"}})
    with pytest.raises(LiveLockError) as exc:
        build_executor(cfg, _PriceProvider(), _repo(tmp_path), live_flag=False)
    assert "LIVE_TRADING=1" in str(exc.value) and "--live" in str(exc.value)


def test_live_rejected_without_cli_flag(tmp_path):
    # .env kilidi açık ama --live yok → yine RED.
    cfg = _cfg({"execution": {"mode": "live"}}, live_trading=True,
               binance_api_key="k", binance_api_secret="s")
    with pytest.raises(LiveLockError) as exc:
        build_executor(cfg, _PriceProvider(), _repo(tmp_path), live_flag=False)
    assert "--live" in str(exc.value)


def test_live_rejected_without_keys(tmp_path):
    # Üç kilit açık ama API anahtarı yok → RED.
    cfg = _cfg({"execution": {"mode": "live"}}, live_trading=True)
    with pytest.raises(LiveLockError) as exc:
        build_executor(cfg, _PriceProvider(), _repo(tmp_path), live_flag=True)
    assert "API" in str(exc.value)


def test_testnet_requires_testnet_keys(tmp_path):
    cfg = _cfg({"execution": {"mode": "testnet"}})
    with pytest.raises(LiveLockError) as exc:
        build_executor(cfg, _PriceProvider(), _repo(tmp_path))
    assert "TESTNET" in str(exc.value)


def test_testnet_builds_with_keys(tmp_path):
    from src.execution.binance_spot import BinanceSpotExecutor

    cfg = _cfg({"execution": {"mode": "testnet"}},
               binance_testnet_api_key="k", binance_testnet_api_secret="s")
    ex = build_executor(cfg, _PriceProvider(), _repo(tmp_path))
    assert isinstance(ex, BinanceSpotExecutor) and ex.mode.value == "testnet"


def test_live_builds_with_all_three_locks(tmp_path):
    from src.execution.binance_spot import BinanceSpotExecutor

    cfg = _cfg({"execution": {"mode": "live"}}, live_trading=True,
               binance_api_key="k", binance_api_secret="s")
    ex = build_executor(cfg, _PriceProvider(), _repo(tmp_path), live_flag=True)
    assert isinstance(ex, BinanceSpotExecutor) and ex.mode.value == "live"

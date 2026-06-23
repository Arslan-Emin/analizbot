"""Executor fabrikası — kademeyi seçer ve CANLI için ÜÇLÜ KİLİDİ uygular.

GÜVENLİK KAPISI (en kritik fonksiyon). Canlı emir yalnız ÜÇ koşul birden
sağlanırsa mümkündür:
  1. `.env` → `LIVE_TRADING=1`            (Settings.live_trading)
  2. `config.yaml` → `execution.mode: live`
  3. CLI → `--live` bayrağı               (live_flag)
Biri eksikse `LiveLockError` yükselir → canlı emir REDDEDİLİR (paper'a sessizce
düşmeyiz; kullanıcı bilinçli açmalı). Ayrıca `execution.enabled` + `watch --execute`
çağıran tarafça (scheduler/CLI) zorunlu kılınır.
"""

from __future__ import annotations

import logging

from src.execution.base import OrderExecutor
from src.execution.models import ExecMode
from src.execution.paper import PaperExecutor

log = logging.getLogger(__name__)

_DEFAULT_PAPER_CAPITAL = 1000.0


class LiveLockError(RuntimeError):
    """Canlı kilit(ler) eksik ya da gerekli anahtarlar yok → emir reddedilir."""


def build_executor(cfg, provider, repo, *, live_flag: bool = False) -> OrderExecutor:
    """config + .env + CLI bayrağına göre doğru OrderExecutor'ı kurar.

    cfg: AppConfig (settings + yaml). provider: fiyat için. repo: paper bakiye/maruziyet.
    """
    ecfg = dict(cfg.yaml.get("execution", {}))
    mode = ExecMode(str(ecfg.get("mode", "paper")).lower())
    quote = str(cfg.yaml.get("quote_currency", "USDT"))

    if mode == ExecMode.PAPER:
        cap = float(ecfg.get("allocation_quote_cap", 0) or 0)
        paper_capital = cap if cap > 0 else _DEFAULT_PAPER_CAPITAL
        log.info("Executor: PAPER (simülasyon, sermaye %.0f %s).", paper_capital, quote)
        return PaperExecutor(provider, repo, paper_capital)

    if mode == ExecMode.TESTNET:
        key = cfg.settings.binance_testnet_api_key
        secret = cfg.settings.binance_testnet_api_secret
        if not (key and secret):
            raise LiveLockError(
                "Testnet için BINANCE_TESTNET_API_KEY ve BINANCE_TESTNET_API_SECRET "
                ".env'de gereklidir."
            )
        from src.execution.binance_spot import BinanceSpotExecutor

        log.info("Executor: TESTNET (Binance sandbox, sahte para).")
        return BinanceSpotExecutor(key, secret, testnet=True, quote=quote, ecfg=ecfg)

    # mode == LIVE → ÜÇLÜ KİLİT
    missing: list[str] = []
    if not cfg.settings.live_trading:
        missing.append("LIVE_TRADING=1 (.env)")
    if not live_flag:
        missing.append("--live (CLI bayrağı)")
    # Üçüncü kilit (config mode: live) bu noktaya gelindiği için zaten sağlandı.
    if missing:
        raise LiveLockError(
            "🛑 CANLI İŞLEM REDDEDİLDİ — eksik güvenlik kilidi: "
            + ", ".join(missing)
            + ". Canlı için ÜÇÜ birden gerekir: LIVE_TRADING=1 + execution.mode: live + --live."
        )

    key = cfg.settings.binance_api_key
    secret = cfg.settings.binance_api_secret
    if not (key and secret):
        raise LiveLockError("Canlı işlem için BINANCE_API_KEY ve BINANCE_API_SECRET gereklidir.")
    from src.execution.binance_spot import BinanceSpotExecutor

    log.warning("Executor: LIVE (GERÇEK PARA). Üçlü kilit açık.")
    return BinanceSpotExecutor(key, secret, testnet=False, quote=quote, ecfg=ecfg)

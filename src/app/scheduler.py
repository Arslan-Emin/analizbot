"""Watch modu: watchlist'i periyodik tara, sinyal DEĞİŞİNCE bildir.

- APScheduler ile `scan_interval_minutes`'ta bir tarama.
- Spam önleme: aynı sinyal tekrar gönderilmez; yalnız BUY↔SELL↔HOLD geçişlerinde
  bildirim atılır (DB'deki son aksiyonla karşılaştırılır).
- Sembol-bazlı hata izolasyonu: bir sembol hata verirse loglanır, diğerleri devam eder.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from apscheduler.schedulers.blocking import BlockingScheduler

from src.config import AppConfig, strategy_params
from src.core.engine import AnalysisEngine
from src.data.market_registry import get_provider
from src.notify.base import Notifier
from src.storage.db import Repository
from src.strategies.registry import build_strategy

log = logging.getLogger(__name__)


class WatchScanner:
    def __init__(
        self,
        cfg: AppConfig,
        notifier: Notifier,
        repo: Repository,
        *,
        calibrate: bool = False,
    ) -> None:
        self.cfg = cfg
        self.notifier = notifier
        self.repo = repo
        self.timeframe = cfg.yaml.get("timeframe", "1h")
        self.watchlist = list(cfg.yaml.get("watchlist", []))
        self.strategy_name = cfg.yaml.get("active_strategy", "ema_rsi")
        self.params = strategy_params(cfg.yaml, self.strategy_name)
        self.calibrator = self._build_calibrator() if calibrate else None

    def _build_calibrator(self):
        """Geçmiş sinyallerden güven kalibratörü kurar (config.learning ayarlarıyla)."""
        from src.learning.calibrator import ConfidenceCalibrator

        lcfg = self.cfg.yaml.get("learning", {})
        return ConfidenceCalibrator.from_history(
            self.repo,
            strategy=self.strategy_name,
            n_bins=int(lcfg.get("calibration_bins", 10)),
            min_samples=int(lcfg.get("min_samples_for_calibration", 30)),
        )

    def scan_once(self) -> None:
        started = datetime.now(UTC)
        errors: list[str] = []
        generated = 0

        for symbol in self.watchlist:
            try:
                # Bildirimden ÖNCE son aksiyonu oku (değişim tespiti için).
                prev_action = self.repo.last_signal_for(symbol)

                provider = get_provider(
                    symbol,
                    api_key=self.cfg.settings.binance_api_key,
                    api_secret=self.cfg.settings.binance_api_secret,
                )
                strategy = build_strategy(self.strategy_name, self.params)
                engine = AnalysisEngine(provider, strategy, calibrator=self.calibrator)
                result = engine.analyze(symbol, timeframe=self.timeframe)

                self.repo.save_signal(result)
                generated += 1

                new_action = result.signal.action
                # Sinyal değiştiyse (veya ilk kez) bildir; aksi halde sessiz kal.
                if prev_action is None or new_action != prev_action:
                    self.notifier.send_signal(result)
                    log.info("%s sinyal: %s -> %s (bildirildi)", symbol, prev_action, new_action)
                else:
                    log.info("%s sinyal değişmedi (%s), bildirim yok", symbol, new_action.value)

            except Exception as exc:  # sembol-bazlı izolasyon
                log.error("%s taranırken hata: %s", symbol, exc)
                errors.append(f"{symbol}: {exc}")

        finished = datetime.now(UTC)
        self.repo.save_run(started, finished, len(self.watchlist), generated, errors)
        log.info(
            "Tarama bitti: %d sembol, %d sinyal, %d hata.",
            len(self.watchlist),
            generated,
            len(errors),
        )


def run_watch(
    cfg: AppConfig, notifier: Notifier, *, once: bool = False, calibrate: bool = False
) -> None:
    repo = Repository(cfg.settings.db_url)
    scanner = WatchScanner(cfg, notifier, repo, calibrate=calibrate)

    if not scanner.watchlist:
        log.warning("watchlist boş; config.yaml'a sembol ekleyin.")
        return

    # İlk taramayı hemen yap (kullanıcı sonucu beklemeden görsün).
    scanner.scan_once()
    if once:
        return

    interval = int(cfg.yaml.get("scan_interval_minutes", 15))
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(scanner.scan_once, "interval", minutes=interval, id="watch_scan")
    log.info(
        "Watch modu: her %d dakikada bir %d sembol taranacak.",
        interval,
        len(scanner.watchlist),
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Watch modu durduruldu.")

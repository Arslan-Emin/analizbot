"""Watch modu: watchlist'i periyodik tara, sinyal DEĞİŞİNCE bildir.

- APScheduler ile `scan_interval_minutes`'ta bir tarama.
- Spam önleme: aynı sinyal tekrar gönderilmez; yalnız BUY↔SELL↔HOLD geçişlerinde
  bildirim atılır (DB'deki son aksiyonla karşılaştırılır).
- Sembol-bazlı hata izolasyonu: bir sembol hata verirse loglanır, diğerleri devam eder.
- Rejim filtresi + dinamik ensemble + Kelly: analyze/screen ile AYNI canlı yol
  (src.app.live_strategy) → otonom işlemler de rejim-filtrelidir.
- Opsiyonel OTONOM İŞLEM: `exec_manager` verilirse her taramada açık pozisyonlar
  mutabakatlanır (stop/TP) ve her sinyal pozisyonla bağdaştırılıp emir verilir/bekletilir.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from apscheduler.schedulers.blocking import BlockingScheduler

from src.app.live_strategy import (
    inject_kelly,
    maybe_dynamic_ensemble,
    regime_cfg,
    resolve_regime_flag,
)
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
        exec_manager=None,
    ) -> None:
        self.cfg = cfg
        self.notifier = notifier
        self.repo = repo
        self.timeframe = cfg.yaml.get("timeframe", "1h")
        # Otonom modda işlem zaman dilimi (config execution.timeframe) global timeframe'i ezer
        # (read-only watch global tf'i kullanır; --execute ile işlem tf'i devreye girer).
        if exec_manager is not None:
            etf = cfg.yaml.get("execution", {}).get("timeframe")
            if etf:
                self.timeframe = str(etf)
        self.watchlist = list(cfg.yaml.get("watchlist", []))
        self.strategy_name = cfg.yaml.get("active_strategy", "ema_rsi")
        self.params = strategy_params(cfg.yaml, self.strategy_name)
        # Dinamik ensemble ağırlıkları (ensemble + dynamic_weight ise) bir kez ayarlanır.
        self.params = maybe_dynamic_ensemble(self.strategy_name, self.params, cfg.settings.db_url)
        self.calibrator = self._build_calibrator() if calibrate else None
        # Opsiyonel otonom işlem yöneticisi (None → read-only, mevcut davranış).
        self.exec_manager = exec_manager
        # Piyasa rejimi filtresi (config.regime.enable). analyze/screen ile aynı.
        self.regime_cfg = regime_cfg(cfg)
        self.regime_enabled = resolve_regime_flag(None, self.regime_cfg)

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

    def _build_regime(self):
        """Tarama başına TEK rejim değerlendirmesi (tüm sembollere uygulanır)."""
        from src.core.regime import build_live_regime, select_breadth_symbols

        provider = get_provider(
            "BTC/USDT",
            api_key=self.cfg.settings.binance_api_key,
            api_secret=self.cfg.settings.binance_api_secret,
        )
        symbols = None
        if bool(self.regime_cfg.get("use_breadth", True)):
            symbols = select_breadth_symbols(
                provider,
                str(self.regime_cfg.get("breadth_quote", "USDT")),
                int(self.regime_cfg.get("breadth_top_n", 30)),
            )
        return build_live_regime(provider, self.regime_cfg, symbols)

    def scan_once(self) -> None:
        started = datetime.now(UTC)
        errors: list[str] = []
        generated = 0

        # Rejim değerlendirmesi (bir kez). Hata olursa kapılama yapmadan devam.
        assessment = None
        if self.regime_enabled:
            try:
                assessment = self._build_regime()
                log.info(
                    "Watch rejim: %s (skor %+.2f)", assessment.state.value, assessment.score
                )
            except Exception as exc:
                log.warning("Rejim değerlendirmesi alınamadı, filtresiz devam: %s", exc)

        # Otonom mod: tarama başında açık pozisyonların stop/TP mutabakatı.
        if self.exec_manager is not None:
            try:
                self.exec_manager.reconcile()
            except Exception as exc:
                log.error("Mutabakat (reconcile) hatası: %s", exc)

        for symbol in self.watchlist:
            try:
                # Bildirimden ÖNCE son aksiyonu oku (değişim tespiti için).
                prev_action = self.repo.last_signal_for(symbol)

                provider = get_provider(
                    symbol,
                    api_key=self.cfg.settings.binance_api_key,
                    api_secret=self.cfg.settings.binance_api_secret,
                )
                params = inject_kelly(
                    self.strategy_name, symbol, self.params, self.cfg.settings.db_url
                )
                strategy = build_strategy(self.strategy_name, params)
                if assessment is not None:
                    from src.core.regime import static_regime_fn
                    from src.strategies.regime_filtered import RegimeFilteredStrategy

                    strategy = RegimeFilteredStrategy(
                        strategy, static_regime_fn(assessment), self.regime_cfg
                    )
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

                # Otonom işlem: sinyali pozisyonla bağdaştır (değişse de değişmese de).
                if self.exec_manager is not None:
                    self.exec_manager.on_signal(result)

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


def _build_exec_manager(cfg: AppConfig, notifier: Notifier, repo: Repository, *, live: bool):
    """watch --execute için executor + ExecutionManager kurar (kapılı + güvenli).

    None döner: execution.enabled=false veya canlı kilit eksikse (LiveLockError) →
    watch read-only sürer.
    """
    from src.execution.factory import LiveLockError, build_executor
    from src.execution.manager import ExecutionManager
    from src.execution.models import DecisionMode

    ecfg = dict(cfg.yaml.get("execution", {}))
    if not ecfg.get("enabled", False):
        log.warning(
            "Otonom işlem istendi ama config execution.enabled=false → READ-ONLY devam."
        )
        return None
    try:
        provider = get_provider(
            "BTC/USDT",
            api_key=cfg.settings.binance_api_key,
            api_secret=cfg.settings.binance_api_secret,
        )
        executor = build_executor(cfg, provider, repo, live_flag=live)
        decision = DecisionMode(str(ecfg.get("decision", "confirm")).lower())
        manager = ExecutionManager(
            repo, executor, notifier, ecfg,
            decision=decision, strategy=cfg.yaml.get("active_strategy", "ema_rsi"),
        )
        log.warning(
            "⚠️ OTONOM İŞLEM ETKİN: mod=%s, karar=%s. (paper=simülasyon)",
            executor.mode.value, decision.value,
        )
        notifier.send_text(
            f"⚙️ Otonom işlem etkin: mod={executor.mode.value}, karar={decision.value}."
        )
        return manager
    except LiveLockError as exc:
        log.error("%s", exc)
        log.warning("Otonom işlem devre dışı → READ-ONLY devam.")
        return None


def run_watch(
    cfg: AppConfig,
    notifier: Notifier,
    *,
    once: bool = False,
    calibrate: bool = False,
    execute: bool = False,
    live: bool = False,
) -> None:
    repo = Repository(cfg.settings.db_url)
    exec_manager = _build_exec_manager(cfg, notifier, repo, live=live) if execute else None
    scanner = WatchScanner(cfg, notifier, repo, calibrate=calibrate, exec_manager=exec_manager)

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

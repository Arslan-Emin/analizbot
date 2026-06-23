"""ExecutionManager — sinyali pozisyonla bağdaştırıp emir hayata geçirir.

Spot long-only, pozisyon-farkında akış (her tarama):
  1. `reconcile()` — açık pozisyonların koruyucu stop'u doldu mu / TP'ye ulaştı mı?
     (paper'da fiyatla simüle; live'da borsayla mutabakat). Dolduysa kapat + PnL.
  2. `on_signal(result)` — sembol için:
       - Pozisyon YOK + BUY → RiskManager onaylarsa: (auto) market alım + koruyucu
         stop; (confirm) PENDING niyet + bildirim.
       - Pozisyon VAR + (SELL/HOLD) → market satışla kapat + stop'u iptal et.
       - Pozisyon VAR + BUY → no-op (zaten long; piramitleme yok).

DB her adımda güncellenir (denetim izi + mutabakat); bildirici kullanıcıyı haberdar
eder. Çekirdek/strateji bu modülü import ETMEZ — yalnız watch döngüsü kullanır.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from src.core.models import Action, AnalysisResult
from src.execution.base import OrderExecutor
from src.execution.models import DecisionMode, OrderIntent, OrderResult, OrderSide, PositionState
from src.execution.risk import RiskManager
from src.notify.base import Notifier
from src.storage.db import Repository

log = logging.getLogger(__name__)


class ExecutionManager:
    """Yürütme kararlarını veren, DB'yi güncelleyen ve bildiren koordinatör."""

    def __init__(
        self,
        repo: Repository,
        executor: OrderExecutor,
        notifier: Notifier,
        ecfg: dict,
        *,
        decision: DecisionMode = DecisionMode.CONFIRM,
        strategy: str = "",
    ) -> None:
        self.repo = repo
        self.executor = executor
        self.notifier = notifier
        self.ecfg = dict(ecfg)
        self.decision = decision
        self.strategy = strategy
        self.mode = executor.mode
        self.risk = RiskManager(ecfg)

    # ------------------------------------------------------------------ #
    # Tarama başı: açık pozisyonların mutabakatı (stop / TP)
    # ------------------------------------------------------------------ #

    def reconcile(self) -> None:
        """Açık tüm pozisyonlar için koruyucu stop/TP çıkışlarını uygular."""
        for pos in self.repo.list_positions(status="open", mode=self.mode.value):
            try:
                self._handle_open_position(pos)
            except Exception as exc:  # pozisyon-bazlı izolasyon
                log.error("%s mutabakatı hata: %s", pos.get("symbol"), exc)

    def _handle_open_position(self, pos: dict) -> None:
        """Bir açık pozisyonun koruyucu stop / TP çıkışını kontrol eder."""
        position = self._to_state(pos)
        # a) Koruyucu stop / dış likidasyon (pasif çıkış — emir göndermeyiz).
        fill = self.executor.poll_protective_exit(position)
        if fill is not None:
            self._finalize_close(pos, fill.price, f"koruyucu stop ({fill.reason})")
            return
        # b) Take-profit (aktif kapatma — market satış).
        tp = pos.get("tp_price")
        if tp is not None:
            try:
                price = self.executor.last_price(pos["symbol"])
            except Exception as exc:
                log.debug("%s TP fiyatı alınamadı: %s", pos["symbol"], exc)
                return
            if price >= tp:
                self._close_active(pos, reason=f"take-profit (fiyat {round(price, 4)} ≥ {tp})")

    # ------------------------------------------------------------------ #
    # Sinyal işleme
    # ------------------------------------------------------------------ #

    def on_signal(self, result: AnalysisResult) -> None:
        """Sinyali mevcut pozisyonla bağdaştırıp gerekli emri verir/bekletir."""
        sig = result.signal
        symbol = sig.symbol
        pos = self.repo.get_open_position(symbol, self.mode.value)

        if pos is not None:
            # Pozisyon VAR: SELL/HOLD → kapat; BUY → no-op (piramitleme yok).
            if sig.action in (Action.SELL, Action.HOLD):
                self._close_active(pos, reason=f"sinyal {sig.action.value}")
            return

        if sig.action == Action.BUY:
            self._try_open(result)

    def _try_open(self, result: AnalysisResult) -> None:
        """BUY sinyali için risk kontrolü + (auto) emir / (confirm) bekleyen niyet."""
        sig = result.signal
        symbol = sig.symbol
        try:
            entry = self.executor.last_price(symbol)
        except Exception as exc:
            log.error("%s fiyatı alınamadı, giriş atlandı: %s", symbol, exc)
            return

        stop_pct = float(self.ecfg.get("stop_loss_pct", 5.0))
        tp_pct = float(self.ecfg.get("take_profit_pct", 10.0))
        stop_price = round(entry * (1.0 - stop_pct / 100.0), 8)
        # Hedef: sinyalin TP'si (varsa) yoksa config take_profit_pct.
        tp_price = sig.take_profit if sig.take_profit else round(entry * (1.0 + tp_pct / 100.0), 8)

        now = datetime.now(UTC)
        decision = self.risk.check_and_size(
            symbol, entry, stop_price,
            free_quote=self.executor.free_quote(),
            open_count=self.repo.count_open_positions(self.mode.value),
            open_exposure=self.repo.open_exposure(self.mode.value),
            daily_pnl=self.repo.get_daily_pnl(now.strftime("%Y-%m-%d"), self.mode.value),
            last_trade_time=self.repo.last_trade_time(symbol, self.mode.value),
            now=now,
        )
        if not decision.approved:
            log.info("%s giriş atlandı: %s", symbol, decision.reason)
            return

        if self.decision == DecisionMode.CONFIRM:
            intent = OrderIntent(
                symbol=symbol, side=OrderSide.BUY, quote_amount=decision.quote_amount,
                reason=decision.reason, stop_price=stop_price, take_profit=tp_price,
                confidence=sig.confidence,
            )
            iid = self.repo.save_pending_intent(intent, self.mode)
            self._notify(
                f"⏳ ONAY BEKLİYOR #{iid} · {symbol} AL {decision.quote_amount:.2f} USDT "
                f"@ ~{round(entry, 4)} · stop {stop_price} · hedef {tp_price} [{self.mode.value}]\n"
                f"Onayla: 'trade approve {iid}'  ·  Reddet: 'trade reject {iid}'"
            )
            log.info("%s onay bekliyor (intent #%d): %s", symbol, iid, decision.reason)
            return

        # AUTO → hemen emir.
        self._open_position(symbol, decision.quote_amount, stop_price, tp_price)

    # ------------------------------------------------------------------ #
    # Onaylı mod: approve / reject
    # ------------------------------------------------------------------ #

    def approve_intent(self, intent_id: int) -> tuple[bool, str]:
        """Bekleyen bir niyeti onaylar ve emri hayata geçirir."""
        intent = self.repo.get_pending_intent(intent_id)
        if intent is None:
            return False, f"Niyet #{intent_id} bulunamadı."
        if intent["status"] != "PENDING":
            return False, f"Niyet #{intent_id} zaten {intent['status']}."
        pid = self._open_position(
            intent["symbol"], intent["quote_amount"], intent["stop_price"], intent["take_profit"]
        )
        self.repo.update_pending_intent(
            intent_id, status="EXECUTED", resolved_at=datetime.now(UTC)
        )
        if pid is None:
            return False, f"Niyet #{intent_id} onaylandı ama emir başarısız (loglara bakın)."
        return True, f"Niyet #{intent_id} onaylandı → pozisyon #{pid} açıldı."

    def reject_intent(self, intent_id: int) -> tuple[bool, str]:
        """Bekleyen bir niyeti reddeder (emir verilmez)."""
        intent = self.repo.get_pending_intent(intent_id)
        if intent is None:
            return False, f"Niyet #{intent_id} bulunamadı."
        if intent["status"] != "PENDING":
            return False, f"Niyet #{intent_id} zaten {intent['status']}."
        self.repo.update_pending_intent(
            intent_id, status="REJECTED", resolved_at=datetime.now(UTC)
        )
        return True, f"Niyet #{intent_id} reddedildi."

    # ------------------------------------------------------------------ #
    # Manuel kapatma + panic
    # ------------------------------------------------------------------ #

    def close_symbol(self, symbol: str) -> tuple[bool, str]:
        """Bir sembolün açık pozisyonunu market satışla kapatır."""
        pos = self.repo.get_open_position(symbol, self.mode.value)
        if pos is None:
            return False, f"{symbol} için açık pozisyon yok."
        self._close_active(pos, reason="manuel kapatma")
        return True, f"{symbol} pozisyonu kapatıldı."

    def panic(self) -> str:
        """ACİL DURDURMA: tüm açık pozisyonları kapat + bekleyen niyetleri reddet."""
        closed = 0
        for pos in self.repo.list_positions(status="open", mode=self.mode.value):
            try:
                self._close_active(pos, reason="PANIC")
                closed += 1
            except Exception as exc:
                log.error("PANIC: %s kapatılamadı: %s", pos.get("symbol"), exc)
        rejected = 0
        for intent in self.repo.list_pending_intents("PENDING", self.mode.value):
            self.repo.update_pending_intent(
                intent["id"], status="REJECTED", resolved_at=datetime.now(UTC)
            )
            rejected += 1
        msg = (
            f"PANIC [{self.mode.value}]: {closed} pozisyon kapatıldı, "
            f"{rejected} niyet reddedildi."
        )
        self._notify(f"🛑 {msg}")
        log.warning(msg)
        return msg

    # ------------------------------------------------------------------ #
    # Düşük seviye: aç / kapat
    # ------------------------------------------------------------------ #

    def _open_position(
        self, symbol: str, quote_amount: float, stop_price: float, tp_price: float | None
    ) -> int | None:
        """Market alım + borsaya koruyucu stop + DB pozisyon kaydı."""
        res = self.executor.buy(symbol, quote_amount)
        self.repo.save_exec_order(res)
        if res.status != "filled":
            self._notify(f"⚠️ ALIŞ BAŞARISIZ {symbol}: {res.error or res.status}")
            log.error("%s alış başarısız: %s", symbol, res.error)
            return None

        entry_price = res.fill_price or res.price
        qty = res.qty
        # Koruyucu STOP_LOSS_LIMIT (offline güvenlik ağı). Hata olsa bile pozisyon açıktır.
        protective_id = None
        try:
            stop_res = self.executor.place_protective_stop(symbol, qty, stop_price)
            self.repo.save_exec_order(stop_res)
            if stop_res.status in ("open", "filled"):
                protective_id = stop_res.exchange_order_id
            else:
                log.warning("%s koruyucu stop konulamadı: %s", symbol, stop_res.error)
        except Exception as exc:
            log.error("%s koruyucu stop hata: %s", symbol, exc)

        pos = PositionState(
            symbol=symbol, entry_price=entry_price, qty=qty, stop_price=stop_price,
            tp_price=tp_price, status="open", protective_order_id=protective_id,
            mode=self.mode, strategy=self.strategy,
        )
        pid = self.repo.save_position(pos)
        stop_note = "" if protective_id else " (UYARI: koruyucu stop YOK)"
        self._notify(
            f"🟢 AÇILIŞ {symbol} [{self.mode.value}]: AL {qty} @ {round(entry_price, 4)} · "
            f"{quote_amount:.2f} USDT · stop {stop_price} · hedef {tp_price}{stop_note}"
        )
        log.info("%s pozisyon açıldı (#%d): %.2f USDT", symbol, pid, quote_amount)
        return pid

    def _close_active(self, pos: dict, reason: str) -> None:
        """Aktif kapatma: koruyucu stop'u iptal et + market satış + DB kapat."""
        symbol = pos["symbol"]
        if pos.get("protective_order_id"):
            try:
                self.executor.cancel(symbol, pos["protective_order_id"])
            except Exception as exc:
                log.warning("%s koruyucu stop iptal edilemedi: %s", symbol, exc)
        res = self.executor.sell_all(symbol, pos["qty"])
        self.repo.save_exec_order(res)
        if res.status != "filled":
            self._notify(f"⚠️ SATIŞ BAŞARISIZ {symbol}: {res.error or res.status}")
            log.error("%s satış başarısız: %s", symbol, res.error)
            return
        exit_price = res.fill_price or res.price
        self._finalize_close(pos, exit_price, reason, sell_result=res)

    def _finalize_close(
        self, pos: dict, exit_price: float, reason: str, *, sell_result: OrderResult | None = None
    ) -> None:
        """Pozisyonu kapalı işaretle + PnL hesapla + günlük PnL'e işle + bildir."""
        symbol = pos["symbol"]
        qty = pos["qty"]
        entry = pos["entry_price"]
        pnl = round((exit_price - entry) * qty, 2)
        now = datetime.now(UTC)

        # Pasif çıkış (stop/likidasyon): denetim izi için sentetik satış kaydı.
        if sell_result is None:
            self.repo.save_exec_order(OrderResult(
                symbol=symbol, side=OrderSide.SELL, type="stop_loss_limit", qty=qty,
                price=exit_price, status="filled", mode=self.mode,
                fill_price=exit_price, quote_spent=round(qty * exit_price, 2),
            ))

        self.repo.update_position(
            pos["id"], status="closed", closed_at=now,
            exit_price=round(exit_price, 8), pnl_quote=pnl,
        )
        self.repo.add_daily_pnl(now.strftime("%Y-%m-%d"), pnl, self.mode.value)
        emoji = "✅" if pnl >= 0 else "🔴"
        self._notify(
            f"{emoji} KAPANIŞ {symbol} [{self.mode.value}]: {reason} @ {round(exit_price, 4)} · "
            f"PnL {pnl:+.2f} USDT"
        )
        log.info("%s pozisyon kapandı: %s · PnL %.2f", symbol, reason, pnl)

    # ------------------------------------------------------------------ #
    # Yardımcılar
    # ------------------------------------------------------------------ #

    def _to_state(self, pos: dict) -> PositionState:
        return PositionState(
            symbol=pos["symbol"], entry_price=pos["entry_price"], qty=pos["qty"],
            stop_price=pos.get("stop_price"), tp_price=pos.get("tp_price"),
            status=pos.get("status", "open"),
            protective_order_id=pos.get("protective_order_id"),
            mode=self.mode, strategy=pos.get("strategy", ""), id=pos.get("id"),
        )

    def _notify(self, text: str) -> None:
        try:
            self.notifier.send_text(text)
        except Exception as exc:  # bildirim hatası işlemi bozmasın
            log.warning("Bildirim gönderilemedi: %s", exc)

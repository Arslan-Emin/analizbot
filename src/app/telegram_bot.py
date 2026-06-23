"""İki yönlü Telegram kontrol botu — uzaktan izleme + komut + otomatik tarama/işlem.

Tek süreçte hem `watch --execute` işini yapar (periyodik tara + emir ver) hem de
Telegram'dan komut kabul eder. Böylece PC/VPS açıkken telefondan tam kontrol:
  /status /positions /pending /approve <id> /reject <id> /close <SEMBOL> /panic /scan

GÜVENLİK: Yalnız `.env`'deki TELEGRAM_CHAT_ID ile EŞLEŞEN sohbet komut verebilir
(başka biri bota yazarsa "yetkisiz" yanıtı alır). Canlı için üçlü kilit (--live)
yine geçerlidir (executor fabrikası uygular).

Komut MANTIĞI saf/test edilebilir `TelegramController`'dadır; PTB (python-telegram-bot)
kablolaması `run_telegram_bot`'ta incedir ve yalnız çalışma anında import edilir.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from src.config import AppConfig
from src.notify.factory import build_notifier
from src.storage.db import Repository

log = logging.getLogger(__name__)


class TelegramController:
    """Telegram komutlarını repo/ExecutionManager üzerinde düz-metin yanıta çevirir."""

    def __init__(self, repo: Repository, exec_manager, ecfg: dict) -> None:
        self.repo = repo
        self.mgr = exec_manager  # None → otonom kapalı (yalnız izleme + reject)
        self.ecfg = dict(ecfg)
        self.mode = str(ecfg.get("mode", "paper")).lower()
        self._provider = None  # mgr yokken anlık fiyat için tembel kurulan read-only sağlayıcı

    def _last_price(self, symbol: str) -> float | None:
        """Pozisyonun anlık fiyatı (executor varsa ondan, yoksa read-only sağlayıcıdan)."""
        try:
            if self.mgr is not None:
                return float(self.mgr.executor.last_price(symbol))
            if self._provider is None:
                from src.data.market_registry import get_provider
                self._provider = get_provider(symbol)
            return float(self._provider.get_ticker(symbol))
        except Exception:
            return None

    # ---- salt-okuma ----

    def help_text(self) -> str:
        return (
            "🤖 analizbot komutları:\n"
            "/status — durum özeti\n"
            "/positions — açık pozisyonlar\n"
            "/pending — onay bekleyenler\n"
            "/approve <id> — niyeti onayla (emir ver)\n"
            "/reject <id> — niyeti reddet\n"
            "/close <SEMBOL> — pozisyonu kapat (örn /close BTC/USDT)\n"
            "/panic — TÜM pozisyonları kapat\n"
            "/scan — şimdi bir tarama yap"
        )

    def status_text(self) -> str:
        open_pos = self.repo.list_positions("open", self.mode)
        exposure = self.repo.open_exposure(self.mode)
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        daily = self.repo.get_daily_pnl(today, self.mode)
        pending = self.repo.list_pending_intents("PENDING", self.mode)
        enabled = "açık" if self.ecfg.get("enabled", False) else "kapalı"
        return (
            f"📊 Durum [{self.mode}]\n"
            f"Otonom: {enabled} · karar: {self.ecfg.get('decision', 'confirm')}\n"
            f"Açık pozisyon: {len(open_pos)}/{self.ecfg.get('max_concurrent_positions', 2)}\n"
            f"Maruziyet: {exposure:.2f}\n"
            f"Bekleyen onay: {len(pending)}\n"
            f"Günlük PnL: {daily:+.2f}"
        )

    def positions_text(self) -> str:
        rows = self.repo.list_positions("open", self.mode)
        if not rows:
            return "Açık pozisyon yok."
        lines = ["📈 Açık pozisyonlar:"]
        for p in rows:
            entry = p["entry_price"]
            price = self._last_price(p["symbol"])
            if price is not None and entry:
                pnl_pct = (price / entry - 1.0) * 100.0
                pnl_quote = (price - entry) * p["qty"]
                mark = "🟢" if pnl_pct >= 0 else "🔴"
                cur = f"anlık {round(price, 4)} · {mark} {pnl_pct:+.2f}% ({pnl_quote:+.2f})"
            else:
                cur = "anlık fiyat alınamadı"
            lines.append(
                f"#{p['id']} {p['symbol']} · giriş {round(entry, 4)} · adet {round(p['qty'], 8)}\n"
                f"   stop {p['stop_price']} · hedef {p['tp_price']}\n"
                f"   {cur}"
            )
        return "\n".join(lines)

    def pending_text(self) -> str:
        rows = self.repo.list_pending_intents("PENDING", self.mode)
        if not rows:
            return "Bekleyen onay yok."
        lines = ["⏳ Bekleyen onaylar:"]
        for it in rows:
            lines.append(
                f"#{it['id']} {it['symbol']} AL {it['quote_amount']:.2f} · "
                f"stop {it['stop_price']} · hedef {it['take_profit']}  "
                f"→ /approve {it['id']} | /reject {it['id']}"
            )
        return "\n".join(lines)

    # ---- eylem ----

    def approve(self, arg: str | None) -> str:
        if self.mgr is None:
            return "Otonom işlem kapalı (execution.enabled=false) — approve kullanılamaz."
        iid = self._parse_id(arg)
        if iid is None:
            return "Kullanım: /approve <id>"
        _, msg = self.mgr.approve_intent(iid)
        return msg

    def reject(self, arg: str | None) -> str:
        iid = self._parse_id(arg)
        if iid is None:
            return "Kullanım: /reject <id>"
        intent = self.repo.get_pending_intent(iid)
        if intent is None:
            return f"Niyet #{iid} bulunamadı."
        if intent["status"] != "PENDING":
            return f"Niyet #{iid} zaten {intent['status']}."
        self.repo.update_pending_intent(iid, status="REJECTED", resolved_at=datetime.now(UTC))
        return f"Niyet #{iid} reddedildi."

    def close(self, arg: str | None) -> str:
        if self.mgr is None:
            return "Otonom işlem kapalı (execution.enabled=false) — close kullanılamaz."
        if not arg or not arg.strip():
            return "Kullanım: /close <SEMBOL>  (örn /close BTC/USDT)"
        _, msg = self.mgr.close_symbol(arg.strip().upper())
        return msg

    def panic(self) -> str:
        if self.mgr is None:
            return "Otonom işlem kapalı (execution.enabled=false) — panic kullanılamaz."
        return self.mgr.panic()

    @staticmethod
    def _parse_id(arg: str | None) -> int | None:
        try:
            return int(str(arg).strip())
        except (TypeError, ValueError):
            return None


def run_telegram_bot(cfg: AppConfig, *, live: bool = False) -> None:
    """Telegram kontrol botunu çalıştırır (bloklar). Token/chat_id .env'den gelir."""
    token = cfg.settings.telegram_bot_token
    chat_id = cfg.settings.telegram_chat_id
    if not (token and chat_id):
        log.error(
            "Telegram botu için .env'de TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID gerekir."
        )
        return

    from src.app.scheduler import WatchScanner, _build_exec_manager

    repo = Repository(cfg.settings.db_url)
    notifier = build_notifier(cfg)  # konsol + Telegram (işlem bildirimleri buradan gider)
    exec_manager = _build_exec_manager(cfg, notifier, repo, live=live)
    scanner = WatchScanner(cfg, notifier, repo, exec_manager=exec_manager)
    controller = TelegramController(repo, exec_manager, dict(cfg.yaml.get("execution", {})))

    import asyncio

    from telegram import Update
    from telegram.ext import Application, CommandHandler, ContextTypes

    authorized = str(chat_id)

    def _authorized(update: Update) -> bool:
        chat = update.effective_chat
        return chat is not None and str(chat.id) == authorized

    async def _reply(update: Update, fn, *args) -> None:
        """Yetki kontrol + (bloklayan) işi thread'de çalıştır + yanıtla."""
        if not _authorized(update):
            await update.message.reply_text("⛔ Yetkisiz.")
            log.warning("Yetkisiz Telegram erişimi: %s", getattr(update.effective_chat, "id", "?"))
            return
        try:
            text = await asyncio.to_thread(fn, *args)
        except Exception as exc:  # komut hatası botu düşürmesin
            text = f"Hata: {exc}"
            log.error("Telegram komut hatası: %s", exc)
        await update.message.reply_text(text)

    async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await _reply(update, controller.help_text)

    async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await _reply(update, controller.status_text)

    async def cmd_positions(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await _reply(update, controller.positions_text)

    async def cmd_pending(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await _reply(update, controller.pending_text)

    async def cmd_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await _reply(update, controller.approve, ctx.args[0] if ctx.args else None)

    async def cmd_reject(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await _reply(update, controller.reject, ctx.args[0] if ctx.args else None)

    async def cmd_close(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await _reply(update, controller.close, ctx.args[0] if ctx.args else None)

    async def cmd_panic(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await _reply(update, controller.panic)

    async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(update):
            await update.message.reply_text("⛔ Yetkisiz.")
            return
        await update.message.reply_text("⏳ Tarama başladı...")
        try:
            await asyncio.to_thread(scanner.scan_once)
            await update.message.reply_text("✅ Tarama bitti. /status ile bak.")
        except Exception as exc:
            await update.message.reply_text(f"Tarama hatası: {exc}")

    async def scan_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            await asyncio.to_thread(scanner.scan_once)
        except Exception as exc:
            log.error("Periyodik tarama hatası: %s", exc)

    async def on_startup(application) -> None:
        mode = controller.mode
        await application.bot.send_message(
            chat_id=authorized,
            text=f"🤖 analizbot başladı [{mode}]. /help ile komutlar.",
        )

    application = Application.builder().token(token).post_init(on_startup).build()
    application.add_handler(CommandHandler(["start", "help"], cmd_help))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("positions", cmd_positions))
    application.add_handler(CommandHandler("pending", cmd_pending))
    application.add_handler(CommandHandler("approve", cmd_approve))
    application.add_handler(CommandHandler("reject", cmd_reject))
    application.add_handler(CommandHandler("close", cmd_close))
    application.add_handler(CommandHandler("panic", cmd_panic))
    application.add_handler(CommandHandler("scan", cmd_scan))

    # Otonom işlem açıksa periyodik tarama+işlem (watch --execute ile aynı iş).
    if exec_manager is not None and scanner.watchlist:
        interval = int(cfg.yaml.get("scan_interval_minutes", 5)) * 60
        application.job_queue.run_repeating(scan_job, interval=interval, first=10)
        log.warning("Telegram botu: otonom tarama her %d sn'de bir.", interval)
    else:
        log.info("Telegram botu: yalnız izleme/komut (otonom tarama kapalı).")

    log.info("Telegram kontrol botu çalışıyor (Ctrl+C ile durur).")
    application.run_polling()

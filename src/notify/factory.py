"""Bildirim kanallarını config'e göre kuran fabrika + çoklu-kanal sarmalayıcı."""

from __future__ import annotations

import logging

from src.core.models import AnalysisResult
from src.notify.base import Notifier
from src.notify.console import ConsoleNotifier

log = logging.getLogger(__name__)


class MultiNotifier(Notifier):
    """Birden çok kanala aynı anda gönderir; bir kanal hatası diğerini etkilemez."""

    def __init__(self, notifiers: list[Notifier]) -> None:
        self._notifiers = notifiers

    def send_signal(self, result: AnalysisResult) -> None:
        for notifier in self._notifiers:
            try:
                notifier.send_signal(result)
            except Exception as exc:
                log.warning("Bildirim kanalı hata verdi: %s", exc)

    def send_text(self, text: str) -> None:
        for notifier in self._notifiers:
            try:
                notifier.send_text(text)
            except Exception as exc:
                log.warning("Bildirim kanalı hata verdi: %s", exc)


def build_notifier(cfg) -> Notifier:
    """config.yaml + .env'e göre konsol (+ varsa Telegram) bildirici kurar."""
    notifiers: list[Notifier] = [ConsoleNotifier()]  # konsol her zaman açık

    notify_cfg = cfg.yaml.get("notify", {})
    telegram_enabled = bool(notify_cfg.get("telegram", False))
    token = cfg.settings.telegram_bot_token
    chat_id = cfg.settings.telegram_chat_id

    if telegram_enabled and token and chat_id:
        # Import burada: telegram kullanılmıyorsa ağır bağımlılığı yüklemeyelim.
        from src.notify.telegram import TelegramNotifier

        notifiers.append(TelegramNotifier(token, chat_id))
        log.info("Telegram bildirimi etkin.")
    elif telegram_enabled:
        log.warning("Telegram açık ama TELEGRAM_BOT_TOKEN/CHAT_ID eksik; sadece konsol.")

    return MultiNotifier(notifiers)

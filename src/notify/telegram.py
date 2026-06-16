"""Telegram bildirimi (python-telegram-bot 22.x).

Tek yönlü bildirim: sinyal/metin gönderir. Token yoksa bu sınıf hiç kurulmaz
(bkz. factory.build_notifier). Gönderim hataları yutulur ve loglanır — bir
bildirim hatası tüm taramayı bozmamalı. Token ASLA loglanmaz.
"""

from __future__ import annotations

import asyncio
import logging

from telegram import Bot

from src.core.models import AnalysisResult
from src.notify.base import Notifier
from src.notify.format import signal_to_text

log = logging.getLogger(__name__)


class TelegramNotifier(Notifier):
    def __init__(self, token: str, chat_id: str) -> None:
        self._token = token
        self._chat_id = chat_id

    def send_signal(self, result: AnalysisResult) -> None:
        self.send_text(signal_to_text(result))

    def send_text(self, text: str) -> None:
        try:
            asyncio.run(self._async_send(text))
        except Exception as exc:  # bildirim hatası akışı durdurmasın
            log.warning("Telegram mesajı gönderilemedi: %s", exc)

    async def _async_send(self, text: str) -> None:
        # PTB v22 async'tir; her gönderimde kısa ömürlü bir Bot bağlamı kullanırız.
        bot = Bot(self._token)
        async with bot:
            await bot.send_message(chat_id=self._chat_id, text=text)

"""Bildirim arayüzü (spec §5.5). Konsol ve Telegram bunu uygular."""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.core.models import AnalysisResult


class Notifier(ABC):
    @abstractmethod
    def send_signal(self, result: AnalysisResult) -> None:
        """Bir analiz sonucunu (sinyal + gerekçe + seviyeler) iletir."""

    @abstractmethod
    def send_text(self, text: str) -> None:
        """Serbest metin mesajı iletir (durum/uyarı vb.)."""

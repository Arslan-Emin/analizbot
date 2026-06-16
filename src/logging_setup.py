"""Yapısal loglama kurulumu. Seviye .env'deki LOG_LEVEL'dan gelir.

ÖNEMLİ: Sırlar (API anahtarı, token) asla loglanmaz — bu modül yalnızca
formatlamayı ayarlar; çağıranlar gizli değer basmamaya dikkat eder.
"""

from __future__ import annotations

import logging


def setup_logging(level: str = "INFO") -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

"""Konfigürasyon: gizli ayarlar (.env) + gizli olmayan ayarlar (config.yaml).

Sırlar (API anahtarı, Telegram token) yalnızca .env'den okunur ve ASLA loglanmaz.
Strateji parametreleri / watchlist gibi gizli olmayan ayarlar config.yaml'dadır.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """`.env` ve ortam değişkenlerinden okunan gizli ayarlar.

    Alan adları, ortam değişkenleriyle büyük/küçük harf duyarsız eşleşir
    (örn. `binance_api_key` ↔ `BINANCE_API_KEY`).
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    binance_api_key: str | None = None       # opsiyonel; verilirse SADECE read-only
    binance_api_secret: str | None = None
    telegram_bot_token: str | None = None     # opsiyonel
    telegram_chat_id: str | None = None
    log_level: str = "INFO"
    db_url: str = "sqlite:///signals.db"


@dataclass
class AppConfig:
    settings: Settings   # gizli ayarlar (.env)
    yaml: dict           # gizli olmayan ayarlar (config.yaml)


def _load_yaml(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config(config_path: str | Path = "config.yaml") -> AppConfig:
    """Hem .env hem config.yaml'ı okuyup tek bir nesnede döndürür."""
    return AppConfig(settings=Settings(), yaml=_load_yaml(config_path))


def strategy_params(yaml_cfg: dict, strategy_name: str | None = None) -> dict:
    """Aktif stratejinin parametrelerini config.yaml'dan çeker.

    timeframe de paramlara eklenir (strateji Signal.timeframe için kullanabilir).
    """
    name = strategy_name or yaml_cfg.get("active_strategy", "ema_rsi")
    params = dict(yaml_cfg.get("strategies", {}).get(name, {}))
    params.setdefault("timeframe", yaml_cfg.get("timeframe", "1h"))
    return params

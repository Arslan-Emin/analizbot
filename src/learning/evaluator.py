"""Sinyal sonuç değerlendirici — açık sinyalleri geçmiş veriyle çözer.

Her BUY/SELL sinyali için, üretildiği andan SONRAKİ barları çeker ve stop/hedef
takibiyle sonucu belirler (`simulate_outcome`, backtest ile aynı semantik). Yeterli
gelecek bar yoksa sinyal OPEN kalır ve sonraki çağrıda tekrar denenir.

Bu, botun "öğrenme" döngüsünün veri toplama adımıdır: sonuçlar DB'ye yazılır,
ardından `stats` ve `calibrator` bunları kullanır.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from src.core.simulate import simulate_outcome
from src.storage.db import Repository

log = logging.getLogger(__name__)


def evaluate_open_signals(
    repo: Repository,
    provider,
    *,
    eval_horizon_bars: int = 48,
    now: datetime | None = None,
) -> dict:
    """Açık sinyalleri değerlendirir, sonuçları DB'ye yazar.

    `provider`: `fetch_ohlcv_range(symbol, timeframe, since_ms, until_ms)` sunan bir
    veri sağlayıcı (tüm semboller için tek sağlayıcı yeterli — Binance).
    Döndürür: {"pending", "resolved", "open", "errors"} sayaçları.
    """
    now = now or datetime.now(UTC)
    now_ms = int(now.timestamp() * 1000)

    pending = repo.unresolved_signals()
    resolved = 0
    still_open = 0
    errors = 0

    for sig in pending:
        try:
            created = sig["created_at"]
            if created.tzinfo is None:  # bazı SQLite kayıtları naive dönebilir
                created = created.replace(tzinfo=UTC)
            since_ms = int(created.timestamp() * 1000)
            if since_ms >= now_ms:
                still_open += 1
                continue

            df = provider.fetch_ohlcv_range(sig["symbol"], sig["timeframe"], since_ms, now_ms)
            future = df[df.index > created] if not df.empty else df
            if future.empty:
                still_open += 1
                continue

            side = "long" if sig["action"] == "BUY" else "short"
            res = simulate_outcome(
                future["high"].to_numpy(),
                future["low"].to_numpy(),
                future["close"].to_numpy(),
                side,
                float(sig["suggested_entry"]),
                float(sig["stop_loss"]),
                float(sig["take_profit"]),
                eval_horizon_bars,
            )

            if res.outcome == "OPEN":
                still_open += 1
                continue

            repo.save_outcome(
                sig["id"],
                res.outcome,
                res.return_pct,
                res.r_multiple,
                res.bars_to_outcome,
                res.exit_price,
                res.exit_reason,
                now,
            )
            resolved += 1
        except Exception as exc:  # sinyal-bazlı izolasyon: biri patlarsa diğerleri sürsün
            errors += 1
            log.warning("Sinyal %s değerlendirilemedi: %s", sig.get("id"), exc)

    return {
        "pending": len(pending),
        "resolved": resolved,
        "open": still_open,
        "errors": errors,
    }

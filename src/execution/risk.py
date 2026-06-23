"""RiskManager — giriş öncesi tüm güvenlik kontrolleri + pozisyon boyutlama.

GÜVENLİK ÇEKİRDEĞİ. Saf ve deterministik (ağsız test edilir): tüm girdiler
(bakiye, açık pozisyon sayısı, maruziyet, günlük PnL, son işlem zamanı) dışarıdan
verilir; karar tek yerde toplanır.

Sıra (herhangi biri RED ise erken çık):
  1. Kill-switch — günlük gerçekleşen zarar `max_daily_loss_pct` tavanını aştı mı?
  2. Eşzamanlı pozisyon limiti (`max_concurrent_positions`).
  3. Cooldown — aynı sembolde son işlemden `cooldown_minutes` geçti mi?
  4. Maruziyet odası — `max_total_exposure_pct` doldu mu?
  5. Boyut — risk%/stop mesafesi; `max_position_pct`, maruziyet odası, nakit ve
     `min_order_usdt` ile kırpılır.

Sermaye tabanı: `allocation_quote_cap>0` ise o sabit tutar; değilse
`serbest_quote + açık_maruziyet` (yönetilen toplam öz sermaye).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class RiskDecision:
    """RiskManager kararı: onay + boyut + gerekçe."""

    approved: bool
    quote_amount: float       # onaylıysa alınacak quote tutarı (USDT)
    reason: str               # red gerekçesi veya onay özeti
    capital: float = 0.0      # boyutlamada kullanılan sermaye tabanı (şeffaflık)


class RiskManager:
    """Execution config'ine göre giriş kararını verir."""

    def __init__(self, ecfg: dict) -> None:
        self.cfg = dict(ecfg)

    def _f(self, key: str, default: float) -> float:
        return float(self.cfg.get(key, default))

    def capital_base(self, free_quote: float, open_exposure: float) -> float:
        """Boyutlama/yüzde-limit tabanı: cap varsa sabit, yoksa serbest + maruziyet."""
        cap = self._f("allocation_quote_cap", 0.0)
        if cap > 0:
            return cap
        return free_quote + open_exposure

    def check_and_size(
        self,
        symbol: str,
        entry: float,
        stop: float,
        *,
        free_quote: float,
        open_count: int,
        open_exposure: float,
        daily_pnl: float,
        last_trade_time: datetime | None = None,
        now: datetime | None = None,
    ) -> RiskDecision:
        """Bir BUY sinyali için tüm limitleri kontrol eder ve boyut hesaplar."""
        now = now or datetime.now(UTC)
        capital = self.capital_base(free_quote, open_exposure)
        if capital <= 0:
            return RiskDecision(False, 0.0, "Sermaye 0 (serbest bakiye/cap yok).", capital)

        # 1) Kill-switch — günlük gerçekleşen zarar tavanı.
        max_daily_loss = capital * self._f("max_daily_loss_pct", 3.0) / 100.0
        if daily_pnl <= -max_daily_loss:
            return RiskDecision(
                False, 0.0,
                f"Kill-switch: günlük PnL {daily_pnl:.2f} ≤ -{max_daily_loss:.2f} "
                f"(tavan %{self._f('max_daily_loss_pct', 3.0):.1f}). Gün sonuna dek giriş yok.",
                capital,
            )

        # 2) Eşzamanlı pozisyon limiti.
        max_positions = int(self._f("max_concurrent_positions", 2))
        if open_count >= max_positions:
            return RiskDecision(
                False, 0.0,
                f"Eşzamanlı pozisyon limiti dolu ({open_count}/{max_positions}).", capital,
            )

        # 3) Cooldown — aynı sembolde işlemler arası bekleme.
        cooldown_min = self._f("cooldown_minutes", 60.0)
        if cooldown_min > 0 and last_trade_time is not None:
            last = (
                last_trade_time if last_trade_time.tzinfo
                else last_trade_time.replace(tzinfo=UTC)
            )
            elapsed_min = (now - last).total_seconds() / 60.0
            if elapsed_min < cooldown_min:
                return RiskDecision(
                    False, 0.0,
                    f"Cooldown: {symbol} son işlemden {elapsed_min:.0f}dk geçti "
                    f"(< {cooldown_min:.0f}dk).",
                    capital,
                )

        # 4) Maruziyet odası.
        max_exposure = capital * self._f("max_total_exposure_pct", 40.0) / 100.0
        room = max_exposure - open_exposure
        if room <= 0:
            return RiskDecision(
                False, 0.0,
                f"Maruziyet dolu (açık {open_exposure:.2f} ≥ tavan {max_exposure:.2f}).",
                capital,
            )

        # 5) Boyut — risk%/stop mesafesi, sonra kırpmalar.
        if entry <= 0 or stop <= 0 or stop >= entry:
            return RiskDecision(
                False, 0.0, f"Geçersiz stop (giriş {entry}, stop {stop}; stop<giriş olmalı).",
                capital,
            )
        stop_dist = (entry - stop) / entry
        risk_amount = capital * self._f("risk_per_trade_pct", 1.0) / 100.0
        size = risk_amount / stop_dist
        size = min(size, capital * self._f("max_position_pct", 20.0) / 100.0)  # tek pozisyon
        size = min(size, room)                                                 # maruziyet odası
        size = min(size, free_quote)                                           # eldeki nakit
        size = round(size, 2)

        min_order = self._f("min_order_usdt", 11.0)
        if size < min_order:
            return RiskDecision(
                False, 0.0,
                f"Boyut {size:.2f} < min_order_usdt {min_order:.2f} → atlandı.", capital,
            )

        return RiskDecision(
            True, size,
            f"Onay: {size:.2f} USDT (sermaye {capital:.2f}, risk %"
            f"{self._f('risk_per_trade_pct', 1.0):.1f}, stop mesafesi %{stop_dist * 100:.1f}).",
            capital,
        )

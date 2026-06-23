"""BinanceSpotExecutor — ccxt ile gerçek (testnet/live) Binance Spot emirleri.

GÜVENLİK: Yalnız fabrika (src.execution.factory) tarafından, kademe + üçlü kilit
doğrulandıktan sonra kurulur. `set_sandbox_mode(True)` → testnet (sahte para).

Spot long-only:
  - buy  : `create_market_buy_order_with_cost` (quote tutarıyla; min-notional kontrolü).
  - sell : `amount_to_precision` + `create_market_sell_order`.
  - stop : borsaya STOP_LOSS_LIMIT satış (offline güvenlik ağı; trigger + limit fiyat).
  - poll : koruyucu emir doldu mu / baz bakiye sıfırlandı mı → mutabakat.

Emir verme (create_*) ASLA otomatik yeniden DENENMEZ (çift-emir riski). Okuma
çağrıları hata verirse güvenli tarafta kalınır (örn. None / 0).
"""

from __future__ import annotations

import logging

from src.execution.base import OrderExecutor
from src.execution.models import ExecMode, ExitFill, OrderResult, OrderSide, PositionState

log = logging.getLogger(__name__)


class BinanceSpotExecutor(OrderExecutor):
    """ccxt üzerinden Binance Spot emir yürütücü (testnet veya canlı)."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        testnet: bool = True,
        quote: str = "USDT",
        ecfg: dict | None = None,
        exchange=None,
    ) -> None:
        self.mode = ExecMode.TESTNET if testnet else ExecMode.LIVE
        self.quote = quote.upper()
        self.ecfg = dict(ecfg or {})
        # STOP_LOSS_LIMIT limit fiyatı, tetik fiyatın bu kadar ALTINA konur (dolum garantisi).
        self.stop_limit_offset_pct = float(self.ecfg.get("stop_limit_offset_pct", 0.5))

        if exchange is not None:
            self._exchange = exchange  # test enjeksiyonu
        else:
            import ccxt

            config = {
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
                "options": {"defaultType": "spot"},
            }
            # NOT: config'i ASLA loglama (apiKey/secret sızdırmamak için).
            self._exchange = ccxt.binance(config)
            if testnet:
                self._exchange.set_sandbox_mode(True)

    # ------------------------------------------------------------------ #
    # Okuma
    # ------------------------------------------------------------------ #

    def last_price(self, symbol: str) -> float:
        return float(self._exchange.fetch_ticker(symbol)["last"])

    def free_quote(self) -> float:
        try:
            bal = self._exchange.fetch_balance()
            return float((bal.get(self.quote) or {}).get("free") or 0.0)
        except Exception as exc:
            log.error("Bakiye alınamadı (%s): %s", self.quote, exc)
            return 0.0

    def _base_free(self, symbol: str) -> float:
        base = symbol.split("/")[0]
        bal = self._exchange.fetch_balance()
        return float((bal.get(base) or {}).get("free") or 0.0)

    # ------------------------------------------------------------------ #
    # Emirler
    # ------------------------------------------------------------------ #

    def buy(self, symbol: str, quote_amount: float) -> OrderResult:
        # Borsa min-notional kontrolü (config min_order_usdt'ten ayrı, kesin kısıt).
        min_cost = self._min_cost(symbol)
        if min_cost is not None and quote_amount < min_cost:
            return OrderResult(
                symbol=symbol, side=OrderSide.BUY, type="market", qty=0.0, price=0.0,
                status="rejected", mode=self.mode,
                error=f"Borsa min-notional {min_cost} > {quote_amount}",
            )
        try:
            order = self._exchange.create_market_buy_order_with_cost(symbol, quote_amount)
        except Exception as exc:
            log.error("%s market alım hatası: %s", symbol, exc)
            return OrderResult(
                symbol=symbol, side=OrderSide.BUY, type="market", qty=0.0, price=0.0,
                status="error", mode=self.mode, error=str(exc),
            )
        filled = float(order.get("filled") or order.get("amount") or 0.0)
        avg = float(order.get("average") or order.get("price") or 0.0)
        cost = float(order.get("cost") or quote_amount)
        return OrderResult(
            symbol=symbol, side=OrderSide.BUY, type="market", qty=filled, price=avg,
            status="filled" if filled > 0 else "error", mode=self.mode,
            exchange_order_id=str(order.get("id")) if order.get("id") else None,
            fill_price=avg or None, quote_spent=cost,
            error=None if filled > 0 else "dolum 0",
        )

    def sell_all(self, symbol: str, qty: float) -> OrderResult:
        try:
            amount = float(self._exchange.amount_to_precision(symbol, qty))
            order = self._exchange.create_market_sell_order(symbol, amount)
        except Exception as exc:
            log.error("%s market satım hatası: %s", symbol, exc)
            return OrderResult(
                symbol=symbol, side=OrderSide.SELL, type="market", qty=qty, price=0.0,
                status="error", mode=self.mode, error=str(exc),
            )
        filled = float(order.get("filled") or order.get("amount") or amount)
        avg = float(order.get("average") or order.get("price") or 0.0)
        return OrderResult(
            symbol=symbol, side=OrderSide.SELL, type="market", qty=filled, price=avg,
            status="filled", mode=self.mode,
            exchange_order_id=str(order.get("id")) if order.get("id") else None,
            fill_price=avg or None, quote_spent=float(order.get("cost") or 0.0) or None,
        )

    def place_protective_stop(
        self, symbol: str, qty: float, stop_price: float
    ) -> OrderResult:
        # STOP_LOSS_LIMIT: tetik (stopPrice) + limit fiyat. Limit, tetiğin biraz altında.
        limit_price = stop_price * (1.0 - self.stop_limit_offset_pct / 100.0)
        try:
            amount = float(self._exchange.amount_to_precision(symbol, qty))
            price = float(self._exchange.price_to_precision(symbol, limit_price))
            trigger = float(self._exchange.price_to_precision(symbol, stop_price))
            order = self._exchange.create_order(
                symbol, "STOP_LOSS_LIMIT", "sell", amount, price,
                {"stopPrice": trigger},
            )
        except Exception as exc:
            log.error("%s koruyucu stop hatası: %s", symbol, exc)
            return OrderResult(
                symbol=symbol, side=OrderSide.SELL, type="stop_loss_limit", qty=qty,
                price=stop_price, status="error", mode=self.mode, error=str(exc),
            )
        return OrderResult(
            symbol=symbol, side=OrderSide.SELL, type="stop_loss_limit", qty=qty,
            price=stop_price, status="open", mode=self.mode,
            exchange_order_id=str(order.get("id")) if order.get("id") else None,
        )

    def cancel(self, symbol: str, order_id: str | None) -> bool:
        if not order_id:
            return False
        try:
            self._exchange.cancel_order(order_id, symbol)
            return True
        except Exception as exc:
            log.warning("%s emir iptal hatası (%s): %s", symbol, order_id, exc)
            return False

    # ------------------------------------------------------------------ #
    # Mutabakat
    # ------------------------------------------------------------------ #

    def poll_protective_exit(self, position: PositionState) -> ExitFill | None:
        """Koruyucu stop doldu mu (fetch_order) / baz bakiye sıfırlandı mı?"""
        oid = position.protective_order_id
        if oid:
            try:
                o = self._exchange.fetch_order(oid, position.symbol)
                status = o.get("status")
                if status == "closed":  # stop tetiklendi ve doldu
                    px = o.get("average") or o.get("price") or position.stop_price
                    return ExitFill(float(px), "stop")
                if status in ("open", None):
                    return None  # hâlâ bekliyor → pozisyon duruyor
            except Exception as exc:
                log.debug("%s koruyucu emir sorgulanamadı: %s", position.symbol, exc)

        # Emir iptal/bilinmiyor → baz bakiyeye bak (dışarıdan kapanmış olabilir).
        try:
            if self._base_free(position.symbol) < position.qty * 0.5:
                exit_px = position.stop_price or self.last_price(position.symbol)
                return ExitFill(float(exit_px), "liquidated")
        except Exception as exc:
            log.debug("%s bakiye mutabakatı atlandı: %s", position.symbol, exc)
        return None

    # ------------------------------------------------------------------ #
    # Yardımcı
    # ------------------------------------------------------------------ #

    def _min_cost(self, symbol: str) -> float | None:
        """Borsa pazar min-notional (quote). Bilinmiyorsa None."""
        try:
            market = self._exchange.market(symbol)
            cost_min = (((market or {}).get("limits") or {}).get("cost") or {}).get("min")
            return float(cost_min) if cost_min is not None else None
        except Exception:
            return None

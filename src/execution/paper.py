"""PaperExecutor — simülasyon yürütücü (API YOK, gerçek para riski YOK).

VARSAYILAN kademe ve tüm birim testlerin temeli. Fiyatı veri sağlayıcıdan (canlı
ticker veya testte mock) alır; dolumları anında ve tam (kayma/fee'siz) simüle eder.

Bakiye modeli: `free_quote = paper_capital − açık pozisyon notional toplamı`
(repo'dan okunur → süreç yeniden başlasa da tutarlı). Koruyucu stop borsada
DURMAZ; bunun yerine `poll_protective_exit` her taramada fiyatı stop ile
karşılaştırarak simüle eder.
"""

from __future__ import annotations

from src.data.base import MarketDataProvider
from src.execution.base import OrderExecutor
from src.execution.models import ExecMode, ExitFill, OrderResult, OrderSide, PositionState
from src.storage.db import Repository


class PaperExecutor(OrderExecutor):
    """Gerçek borsa olmadan emirleri simüle eder (provider'dan fiyat alır)."""

    mode = ExecMode.PAPER

    def __init__(
        self,
        provider: MarketDataProvider,
        repo: Repository,
        paper_capital: float = 1000.0,
    ) -> None:
        self.provider = provider
        self.repo = repo
        self.paper_capital = float(paper_capital)

    def last_price(self, symbol: str) -> float:
        return float(self.provider.get_ticker(symbol))

    def free_quote(self) -> float:
        """Başlangıç sermayesinden açık pozisyonların giriş-notional'ı düşülür."""
        deployed = self.repo.open_exposure(self.mode.value)
        return round(max(0.0, self.paper_capital - deployed), 2)

    def buy(self, symbol: str, quote_amount: float) -> OrderResult:
        price = self.last_price(symbol)
        qty = quote_amount / price if price > 0 else 0.0
        return OrderResult(
            symbol=symbol, side=OrderSide.BUY, type="market", qty=round(qty, 8),
            price=price, status="filled", mode=self.mode,
            exchange_order_id=None, fill_price=price, quote_spent=round(quote_amount, 2),
        )

    def sell_all(self, symbol: str, qty: float) -> OrderResult:
        price = self.last_price(symbol)
        return OrderResult(
            symbol=symbol, side=OrderSide.SELL, type="market", qty=round(qty, 8),
            price=price, status="filled", mode=self.mode,
            exchange_order_id=None, fill_price=price, quote_spent=round(qty * price, 2),
        )

    def place_protective_stop(
        self, symbol: str, qty: float, stop_price: float
    ) -> OrderResult:
        # Paper'da borsa emri yok; "open" işaretli sanal kayıt — poll ile simüle edilir.
        return OrderResult(
            symbol=symbol, side=OrderSide.SELL, type="stop_loss_limit", qty=round(qty, 8),
            price=stop_price, status="open", mode=self.mode,
            exchange_order_id=f"paper-stop-{symbol}",
        )

    def cancel(self, symbol: str, order_id: str | None) -> bool:
        return True  # paper'da iptal edilecek gerçek emir yok

    def poll_protective_exit(self, position: PositionState) -> ExitFill | None:
        """Fiyat koruyucu stop'u kırdıysa stop fiyatından dolum simüle eder."""
        if position.stop_price is None:
            return None
        try:
            price = self.last_price(position.symbol)
        except Exception:
            return None
        if price <= position.stop_price:
            return ExitFill(price=position.stop_price, reason="stop")
        return None

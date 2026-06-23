"""Emir yürütücü arayüzü (OrderExecutor ABC).

KRİTİK TASARIM KURALI: ExecutionManager bu ARAYÜZE bağımlıdır, somut paper/binance
sınıfına değil. Yeni kademe/borsa eklemek = yeni bir alt sınıf yazmak; manager
hiç değişmez (PaperExecutor / BinanceSpotExecutor / ileride futures).

Spot long-only sözleşmesi:
  - `buy`  : verilen quote tutarıyla market alım (pozisyon açar).
  - `sell_all` : verilen baz adediyle market satım (pozisyonu kapatır).
  - `place_protective_stop` : girişten sonra borsaya STOP_LOSS_LIMIT satış (offline ağ).
  - `poll_protective_exit` : koruyucu stop dolduysa/likide olduysa dolumu bildirir.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.execution.models import ExecMode, ExitFill, OrderResult, PositionState


class OrderExecutor(ABC):
    """Tüm kademeler (paper/testnet/live) bu soyut sınıfı uygular."""

    mode: ExecMode

    @abstractmethod
    def last_price(self, symbol: str) -> float:
        """Sembolün anlık fiyatı (boyutlama + TP kontrolü için)."""

    @abstractmethod
    def free_quote(self) -> float:
        """Kullanılabilir quote (USDT) bakiyesi. Paper: simüle; live: gerçek bakiye."""

    @abstractmethod
    def buy(self, symbol: str, quote_amount: float) -> OrderResult:
        """Verilen quote tutarıyla market alım. Pozisyon açar."""

    @abstractmethod
    def sell_all(self, symbol: str, qty: float) -> OrderResult:
        """Verilen baz adediyle market satım. Pozisyonu kapatır."""

    @abstractmethod
    def place_protective_stop(
        self, symbol: str, qty: float, stop_price: float
    ) -> OrderResult:
        """Borsaya koruyucu STOP_LOSS_LIMIT satış emri koyar (offline güvenlik ağı)."""

    @abstractmethod
    def cancel(self, symbol: str, order_id: str | None) -> bool:
        """Bir emri iptal eder (örn. koruyucu stop). Başarılıysa True."""

    def poll_protective_exit(self, position: PositionState) -> ExitFill | None:
        """Koruyucu stop dolduysa/dışarıdan likide olduysa kapanış dolumunu döndürür.

        Varsayılan: None (alt sınıf override eder). Paper fiyatı stop'la karşılaştırır;
        live borsadaki bakiye/emir durumuna bakar. TP burada DEĞİL — ExecutionManager
        TP'yi aktif olarak (sell_all) kapatır.
        """
        return None

    def reconcile(self) -> None:  # noqa: B027  (bilinçli opsiyonel hook; paper no-op)
        """Borsa durumunu tazeler (ör. bakiye/emir önbelleği). Varsayılan: no-op."""

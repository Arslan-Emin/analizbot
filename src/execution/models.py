"""Emir yürütme veri modelleri.

Piyasadan bağımsız (Signal/AnalysisResult gibi): paper/testnet/live ve ileride
futures aynı tipleri kullanır. Enum'lar `str` tabanlıdır → JSON/DB yazımı kolay.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum


class ExecMode(str, Enum):  # noqa: UP042  (Action ile tutarlı: str+Enum, JSON/DB kolaylığı)
    """Emir yürütme kademesi (güvenlik için ayrık)."""

    PAPER = "paper"       # simülasyon, API yok — VARSAYILAN
    TESTNET = "testnet"   # Binance testnet, sahte para
    LIVE = "live"         # gerçek para — yalnız üçlü kilitle


class DecisionMode(str, Enum):  # noqa: UP042
    """Karar modu: onaylı (kullanıcı approve eder) veya otonom (bot kendi açar)."""

    CONFIRM = "confirm"
    AUTO = "auto"


class OrderSide(str, Enum):  # noqa: UP042
    """Emir yönü. Spot long-only: BUY=pozisyon aç, SELL=pozisyon kapat."""

    BUY = "BUY"
    SELL = "SELL"


def _now_utc() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class OrderIntent:
    """Bir emir niyeti (henüz gönderilmemiş). Onaylı modda DB'ye yazılır."""

    symbol: str
    side: OrderSide
    quote_amount: float                 # alımda harcanacak quote (USDT); satımda 0 = tüm pozisyon
    reason: str = ""
    stop_price: float | None = None     # alımda hedeflenen koruyucu stop
    take_profit: float | None = None
    confidence: float | None = None
    created_at: datetime = field(default_factory=_now_utc)


@dataclass
class OrderResult:
    """Verilen bir emrin sonucu (paper simülasyonu veya gerçek borsa)."""

    symbol: str
    side: OrderSide
    type: str                           # "market" | "stop_loss_limit"
    qty: float                          # baz varlık adedi (örn BTC)
    price: float                        # referans/dolum fiyatı
    status: str                         # "filled" | "open" | "rejected" | "error"
    mode: ExecMode
    exchange_order_id: str | None = None
    fill_price: float | None = None
    quote_spent: float | None = None    # alımda harcanan quote
    error: str | None = None
    created_at: datetime = field(default_factory=_now_utc)


@dataclass(frozen=True)
class ExitFill:
    """Bir pozisyonun kapanış dolumu (koruyucu stop / dış likidasyon / TP)."""

    price: float
    reason: str   # "stop" | "tp" | "liquidated"


@dataclass
class PositionState:
    """Açık (veya kapanmış) bir spot pozisyon. DB'deki exec_positions ile eşlenir."""

    symbol: str
    entry_price: float
    qty: float
    stop_price: float | None = None
    tp_price: float | None = None
    status: str = "open"                # "open" | "closed"
    protective_order_id: str | None = None
    opened_at: datetime = field(default_factory=_now_utc)
    closed_at: datetime | None = None
    exit_price: float | None = None
    pnl_quote: float | None = None
    mode: ExecMode = ExecMode.PAPER
    strategy: str = ""
    id: int | None = None               # DB satır id (kayıtlıysa)

    @property
    def notional(self) -> float:
        """Giriş anındaki quote değeri (maruziyet hesabı için)."""
        return self.entry_price * self.qty

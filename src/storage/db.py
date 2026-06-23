"""Kalıcılık: SQLite + SQLAlchemy 2.0.

İki tablo:
  - signals        : üretilen her sinyal (geçmiş + watch değişim tespiti için)
  - analysis_runs  : her tarama turunun logu (kaç sembol, kaç sinyal, hatalar)
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from src.core.models import Action, AnalysisResult
from src.execution.models import ExecMode, OrderIntent, OrderResult, PositionState

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class SignalRow(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(8))
    confidence: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    timeframe: Mapped[str] = mapped_column(String(8))
    suggested_entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    suggested_size_quote: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasons: Mapped[str] = mapped_column(Text)  # JSON listesi olarak saklanır
    market: Mapped[str] = mapped_column(String(16), default="crypto")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # --- Öğrenme/geri besleme: sinyal üretildiğinde NULL, sonradan değerlendirilir ---
    strategy: Mapped[str] = mapped_column(String(32), default="ema_rsi", index=True)
    outcome: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    realized_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    r_multiple: Mapped[float | None] = mapped_column(Float, nullable=True)
    bars_to_outcome: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# Var olan signals.db'ler için eklenebilir kolonlar (idempotent migrasyon).
# create_all yeni DB'de bunları zaten kurar; eski DB'de ALTER ile eklenir.
_NEW_SIGNAL_COLUMNS = {
    "strategy": "VARCHAR(32)",
    "outcome": "VARCHAR(8)",
    "realized_return_pct": "FLOAT",
    "r_multiple": "FLOAT",
    "bars_to_outcome": "INTEGER",
    "exit_price": "FLOAT",
    "exit_reason": "VARCHAR(16)",
    "evaluated_at": "DATETIME",
}


class AnalysisRunRow(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    symbols_scanned: Mapped[int] = mapped_column(Integer, default=0)
    signals_generated: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[str] = mapped_column(Text, default="[]")  # JSON listesi


class ThesisRow(Base):
    """Bir yatırım tezinin yaşam döngüsü (IDEA→ENTRY_READY→ACTIVE→CLOSED/INVALIDATED).

    Sinyalden farklı bir KATMAN: kullanıcının takip ettiği fikir + sonucu + MAE/MFE
    postmortemi. Yeni tablo → create_all otomatik kurar (ALTER gerekmez).
    """

    __tablename__ = "theses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    strategy: Mapped[str] = mapped_column(String(32), default="manual")
    direction: Mapped[str] = mapped_column(String(8))  # long / short
    state: Mapped[str] = mapped_column(String(16), index=True, default="IDEA")
    thesis: Mapped[str] = mapped_column(Text, default="")  # serbest metin gerekçe
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    mae_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    mfe_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    signal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # ilgili sinyal (varsa)


class ExecOrderRow(Base):
    """Verilen her emir (paper veya gerçek). Denetim izi + idempotensi için."""

    __tablename__ = "exec_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))            # BUY | SELL
    type: Mapped[str] = mapped_column(String(20))           # market | stop_loss_limit
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(12))         # filled | open | rejected | error
    mode: Mapped[str] = mapped_column(String(8), index=True)  # paper | testnet | live
    exchange_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    quote_spent: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExecPositionRow(Base):
    """Açık/kapalı spot pozisyon (long-only). Mutabakatın ana kaydı."""

    __tablename__ = "exec_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    entry_price: Mapped[float] = mapped_column(Float)
    qty: Mapped[float] = mapped_column(Float)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(8), index=True, default="open")  # open | closed
    protective_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pnl_quote: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    strategy: Mapped[str] = mapped_column(String(32), default="")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PendingIntentRow(Base):
    """Onaylı modda bekleyen emir niyeti (kullanıcı approve/reject eder)."""

    __tablename__ = "pending_intents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))
    quote_amount: Mapped[float] = mapped_column(Float)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    # status: PENDING | APPROVED | REJECTED | EXECUTED
    status: Mapped[str] = mapped_column(String(10), index=True, default="PENDING")
    mode: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExecDailyPnlRow(Base):
    """Günlük gerçekleşen PnL (kill-switch için). (day, mode) başına tek satır."""

    __tablename__ = "exec_daily_pnl"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[str] = mapped_column(String(10), index=True)   # "YYYY-MM-DD" (UTC)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    realized_pnl_quote: Mapped[float] = mapped_column(Float, default=0.0)
    trades: Mapped[int] = mapped_column(Integer, default=0)


class Repository:
    """SQLite üzerinde sinyal/tarama kayıtları için basit depo (repository)."""

    def __init__(self, db_url: str = "sqlite:///signals.db") -> None:
        self.engine = create_engine(db_url)
        # Tablolar yoksa oluştur (idempotent).
        Base.metadata.create_all(self.engine)
        # Var olan eski DB'lere eksik kolonları ekle (Alembic yok; hafif migrasyon).
        self._ensure_columns()

    def _ensure_columns(self) -> None:
        """`signals` tablosunda eksik öğrenme kolonlarını ALTER ile ekler (idempotent)."""
        insp = inspect(self.engine)
        existing = {c["name"] for c in insp.get_columns("signals")}
        missing = {k: v for k, v in _NEW_SIGNAL_COLUMNS.items() if k not in existing}
        if not missing:
            return
        with self.engine.begin() as conn:
            for name, sqltype in missing.items():
                conn.execute(text(f"ALTER TABLE signals ADD COLUMN {name} {sqltype}"))
        log.info("signals tablosuna eklenen kolonlar: %s", list(missing))

    def save_signal(self, result: AnalysisResult) -> int:
        """Bir AnalysisResult'ın sinyalini kaydeder, satır id'sini döndürür."""
        s = result.signal
        with Session(self.engine) as session:
            row = SignalRow(
                symbol=s.symbol,
                action=s.action.value,
                confidence=s.confidence,
                price=s.price,
                timeframe=s.timeframe,
                suggested_entry=s.suggested_entry,
                stop_loss=s.stop_loss,
                take_profit=s.take_profit,
                suggested_size_quote=s.suggested_size_quote,
                reasons=json.dumps(s.reasons, ensure_ascii=False),
                market=result.market,
                created_at=s.created_at,
                strategy=result.strategy,
            )
            session.add(row)
            session.commit()
            return row.id

    def last_signal_for(self, symbol: str) -> Action | None:
        """Bir sembol için en son kaydedilen aksiyon (watch'ta değişim tespiti)."""
        with Session(self.engine) as session:
            stmt = (
                select(SignalRow.action)
                .where(SignalRow.symbol == symbol)
                .order_by(SignalRow.id.desc())
                .limit(1)
            )
            value = session.execute(stmt).scalar_one_or_none()
            return Action(value) if value is not None else None

    def save_run(
        self,
        started_at: datetime,
        finished_at: datetime | None,
        symbols_scanned: int,
        signals_generated: int,
        errors: list[str] | None = None,
    ) -> int:
        """Bir tarama turunun özetini loglar."""
        with Session(self.engine) as session:
            row = AnalysisRunRow(
                started_at=started_at,
                finished_at=finished_at,
                symbols_scanned=symbols_scanned,
                signals_generated=signals_generated,
                errors=json.dumps(errors or [], ensure_ascii=False),
            )
            session.add(row)
            session.commit()
            return row.id

    # ----------------------------- Öğrenme / geri besleme -----------------------------

    def unresolved_signals(self, limit: int = 500) -> list[dict]:
        """Sonucu henüz hesaplanmamış, seviyeleri tam BUY/SELL sinyallerini döndürür.

        Değerlendirici (evaluator) bunları geçmiş veriyle çözer. HOLD ve seviyesiz
        sinyaller (stop/hedef yok) dışarıda bırakılır.
        """
        with Session(self.engine) as session:
            stmt = (
                select(
                    SignalRow.id,
                    SignalRow.symbol,
                    SignalRow.action,
                    SignalRow.timeframe,
                    SignalRow.suggested_entry,
                    SignalRow.stop_loss,
                    SignalRow.take_profit,
                    SignalRow.created_at,
                    SignalRow.strategy,
                )
                .where(
                    SignalRow.outcome.is_(None),
                    SignalRow.action.in_([Action.BUY.value, Action.SELL.value]),
                    SignalRow.suggested_entry.is_not(None),
                    SignalRow.stop_loss.is_not(None),
                    SignalRow.take_profit.is_not(None),
                )
                .order_by(SignalRow.id.asc())
                .limit(limit)
            )
            rows = session.execute(stmt).all()
            return [dict(r._mapping) for r in rows]

    def save_outcome(
        self,
        signal_id: int,
        outcome: str,
        realized_return_pct: float | None,
        r_multiple: float | None,
        bars_to_outcome: int | None,
        exit_price: float | None,
        exit_reason: str | None,
        evaluated_at: datetime,
    ) -> None:
        """Bir sinyalin değerlendirilmiş sonucunu günceller."""
        with Session(self.engine) as session:
            session.execute(
                update(SignalRow)
                .where(SignalRow.id == signal_id)
                .values(
                    outcome=outcome,
                    realized_return_pct=realized_return_pct,
                    r_multiple=r_multiple,
                    bars_to_outcome=bars_to_outcome,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    evaluated_at=evaluated_at,
                )
            )
            session.commit()

    def outcomes(
        self, strategy: str | None = None, symbol: str | None = None
    ) -> list[dict]:
        """Çözülmüş sinyalleri (WIN/LOSS/EXPIRED) istatistik/kalibrasyon için döndürür."""
        with Session(self.engine) as session:
            stmt = select(
                SignalRow.strategy,
                SignalRow.symbol,
                SignalRow.action,
                SignalRow.confidence,
                SignalRow.outcome,
                SignalRow.realized_return_pct,
                SignalRow.r_multiple,
            ).where(SignalRow.outcome.in_(["WIN", "LOSS", "EXPIRED"]))
            if strategy:
                stmt = stmt.where(SignalRow.strategy == strategy)
            if symbol:
                stmt = stmt.where(SignalRow.symbol == symbol)
            rows = session.execute(stmt).all()
            return [dict(r._mapping) for r in rows]

    # ----------------------------- Tez (thesis) takibi -----------------------------

    def create_thesis(
        self,
        symbol: str,
        direction: str,
        thesis: str = "",
        *,
        strategy: str = "manual",
        entry_price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        signal_id: int | None = None,
    ) -> int:
        """Yeni bir tez kaydı oluşturur (state=IDEA), id döndürür."""
        now = datetime.now(UTC)
        with Session(self.engine) as session:
            row = ThesisRow(
                symbol=symbol,
                strategy=strategy,
                direction=direction,
                state="IDEA",
                thesis=thesis,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                created_at=now,
                updated_at=now,
                signal_id=signal_id,
            )
            session.add(row)
            session.commit()
            return row.id

    def get_thesis(self, thesis_id: int) -> dict | None:
        """Tek bir tezi sözlük olarak döndürür (yoksa None)."""
        with Session(self.engine) as session:
            row = session.get(ThesisRow, thesis_id)
            return self._thesis_dict(row) if row else None

    def list_theses(self, state: str | None = None) -> list[dict]:
        """Tezleri (opsiyonel duruma göre) en yeni önce döndürür."""
        with Session(self.engine) as session:
            stmt = select(ThesisRow).order_by(ThesisRow.id.desc())
            if state:
                stmt = stmt.where(ThesisRow.state == state)
            return [self._thesis_dict(r) for r in session.execute(stmt).scalars().all()]

    def update_thesis(self, thesis_id: int, **fields) -> None:
        """Bir tezin alanlarını günceller; updated_at otomatik tazelenir."""
        fields["updated_at"] = datetime.now(UTC)
        with Session(self.engine) as session:
            session.execute(
                update(ThesisRow).where(ThesisRow.id == thesis_id).values(**fields)
            )
            session.commit()

    @staticmethod
    def _thesis_dict(row: ThesisRow) -> dict:
        return {
            "id": row.id, "symbol": row.symbol, "strategy": row.strategy,
            "direction": row.direction, "state": row.state, "thesis": row.thesis,
            "entry_price": row.entry_price, "stop_loss": row.stop_loss,
            "take_profit": row.take_profit, "created_at": row.created_at,
            "updated_at": row.updated_at, "closed_at": row.closed_at,
            "exit_price": row.exit_price, "realized_return_pct": row.realized_return_pct,
            "mae_pct": row.mae_pct, "mfe_pct": row.mfe_pct, "signal_id": row.signal_id,
        }

    # ----------------------------- Emir yürütme (execution) -----------------------------

    def save_exec_order(self, result: OrderResult) -> int:
        """Verilen bir emrin sonucunu denetim izine kaydeder; satır id'sini döndürür."""
        with Session(self.engine) as session:
            row = ExecOrderRow(
                symbol=result.symbol,
                side=result.side.value,
                type=result.type,
                qty=result.qty,
                price=result.price,
                status=result.status,
                mode=result.mode.value,
                exchange_order_id=result.exchange_order_id,
                fill_price=result.fill_price,
                quote_spent=result.quote_spent,
                error=result.error,
                created_at=result.created_at,
            )
            session.add(row)
            session.commit()
            return row.id

    def list_exec_orders(
        self, symbol: str | None = None, mode: str | None = None, limit: int = 200
    ) -> list[dict]:
        """En yeni emirleri (opsiyonel sembol/mod filtresiyle) döndürür."""
        with Session(self.engine) as session:
            stmt = select(ExecOrderRow).order_by(ExecOrderRow.id.desc())
            if symbol:
                stmt = stmt.where(ExecOrderRow.symbol == symbol)
            if mode:
                stmt = stmt.where(ExecOrderRow.mode == mode)
            stmt = stmt.limit(limit)
            return [self._exec_order_dict(r) for r in session.execute(stmt).scalars().all()]

    def save_position(self, pos: PositionState) -> int:
        """Yeni bir açık pozisyon kaydeder; satır id'sini döndürür."""
        with Session(self.engine) as session:
            row = ExecPositionRow(
                symbol=pos.symbol,
                entry_price=pos.entry_price,
                qty=pos.qty,
                stop_price=pos.stop_price,
                tp_price=pos.tp_price,
                status=pos.status,
                protective_order_id=pos.protective_order_id,
                pnl_quote=pos.pnl_quote,
                exit_price=pos.exit_price,
                mode=pos.mode.value,
                strategy=pos.strategy,
                opened_at=pos.opened_at,
                closed_at=pos.closed_at,
            )
            session.add(row)
            session.commit()
            return row.id

    def update_position(self, position_id: int, **fields) -> None:
        """Bir pozisyonun alanlarını günceller (kapatma, stop id, vb.)."""
        with Session(self.engine) as session:
            session.execute(
                update(ExecPositionRow).where(ExecPositionRow.id == position_id).values(**fields)
            )
            session.commit()

    def get_open_position(self, symbol: str, mode: str) -> dict | None:
        """Bir sembol için açık pozisyonu döndürür (yoksa None)."""
        with Session(self.engine) as session:
            stmt = (
                select(ExecPositionRow)
                .where(
                    ExecPositionRow.symbol == symbol,
                    ExecPositionRow.mode == mode,
                    ExecPositionRow.status == "open",
                )
                .order_by(ExecPositionRow.id.desc())
                .limit(1)
            )
            row = session.execute(stmt).scalars().first()
            return self._position_dict(row) if row else None

    def list_positions(self, status: str | None = None, mode: str | None = None) -> list[dict]:
        """Pozisyonları (opsiyonel durum/mod filtresiyle) en yeni önce döndürür."""
        with Session(self.engine) as session:
            stmt = select(ExecPositionRow).order_by(ExecPositionRow.id.desc())
            if status:
                stmt = stmt.where(ExecPositionRow.status == status)
            if mode:
                stmt = stmt.where(ExecPositionRow.mode == mode)
            return [self._position_dict(r) for r in session.execute(stmt).scalars().all()]

    def count_open_positions(self, mode: str) -> int:
        """Açık pozisyon sayısı (max_concurrent_positions limiti için)."""
        with Session(self.engine) as session:
            stmt = select(ExecPositionRow.id).where(
                ExecPositionRow.status == "open", ExecPositionRow.mode == mode
            )
            return len(session.execute(stmt).all())

    def open_exposure(self, mode: str) -> float:
        """Tüm açık pozisyonların giriş-notional toplamı (maruziyet limiti için)."""
        with Session(self.engine) as session:
            stmt = select(ExecPositionRow.entry_price, ExecPositionRow.qty).where(
                ExecPositionRow.status == "open", ExecPositionRow.mode == mode
            )
            return round(sum(p * q for p, q in session.execute(stmt).all()), 2)

    def last_trade_time(self, symbol: str, mode: str) -> datetime | None:
        """Bir sembolde verilen son emir zamanı (cooldown için). Yoksa None."""
        with Session(self.engine) as session:
            stmt = (
                select(ExecOrderRow.created_at)
                .where(ExecOrderRow.symbol == symbol, ExecOrderRow.mode == mode)
                .order_by(ExecOrderRow.id.desc())
                .limit(1)
            )
            return session.execute(stmt).scalar_one_or_none()

    def save_pending_intent(self, intent: OrderIntent, mode: ExecMode) -> int:
        """Onaylı modda bir emir niyetini PENDING olarak kaydeder; id döndürür."""
        with Session(self.engine) as session:
            row = PendingIntentRow(
                symbol=intent.symbol,
                side=intent.side.value,
                quote_amount=intent.quote_amount,
                stop_price=intent.stop_price,
                take_profit=intent.take_profit,
                confidence=intent.confidence,
                reason=intent.reason,
                status="PENDING",
                mode=mode.value,
                created_at=intent.created_at,
            )
            session.add(row)
            session.commit()
            return row.id

    def list_pending_intents(
        self, status: str | None = "PENDING", mode: str | None = None
    ) -> list[dict]:
        """Bekleyen emir niyetlerini döndürür (varsayılan: yalnız PENDING)."""
        with Session(self.engine) as session:
            stmt = select(PendingIntentRow).order_by(PendingIntentRow.id.desc())
            if status:
                stmt = stmt.where(PendingIntentRow.status == status)
            if mode:
                stmt = stmt.where(PendingIntentRow.mode == mode)
            return [self._pending_dict(r) for r in session.execute(stmt).scalars().all()]

    def get_pending_intent(self, intent_id: int) -> dict | None:
        """Tek bir bekleyen niyeti döndürür (yoksa None)."""
        with Session(self.engine) as session:
            row = session.get(PendingIntentRow, intent_id)
            return self._pending_dict(row) if row else None

    def update_pending_intent(self, intent_id: int, **fields) -> None:
        """Bir niyetin durumunu günceller (APPROVED/REJECTED/EXECUTED + resolved_at)."""
        with Session(self.engine) as session:
            session.execute(
                update(PendingIntentRow).where(PendingIntentRow.id == intent_id).values(**fields)
            )
            session.commit()

    def add_daily_pnl(
        self, day: str, pnl_delta: float, mode: str, trades_delta: int = 1
    ) -> None:
        """Günlük gerçekleşen PnL'i artırır (upsert). Kill-switch bunu okur."""
        with Session(self.engine) as session:
            stmt = select(ExecDailyPnlRow).where(
                ExecDailyPnlRow.day == day, ExecDailyPnlRow.mode == mode
            )
            row = session.execute(stmt).scalars().first()
            if row is None:
                row = ExecDailyPnlRow(
                    day=day, mode=mode, realized_pnl_quote=pnl_delta, trades=trades_delta
                )
                session.add(row)
            else:
                row.realized_pnl_quote += pnl_delta
                row.trades += trades_delta
            session.commit()

    def get_daily_pnl(self, day: str, mode: str) -> float:
        """Bir günün gerçekleşen PnL toplamı (kayıt yoksa 0.0)."""
        with Session(self.engine) as session:
            stmt = select(ExecDailyPnlRow.realized_pnl_quote).where(
                ExecDailyPnlRow.day == day, ExecDailyPnlRow.mode == mode
            )
            return float(session.execute(stmt).scalar_one_or_none() or 0.0)

    @staticmethod
    def _exec_order_dict(row: ExecOrderRow) -> dict:
        return {
            "id": row.id, "symbol": row.symbol, "side": row.side, "type": row.type,
            "qty": row.qty, "price": row.price, "status": row.status, "mode": row.mode,
            "exchange_order_id": row.exchange_order_id, "fill_price": row.fill_price,
            "quote_spent": row.quote_spent, "error": row.error, "created_at": row.created_at,
        }

    @staticmethod
    def _position_dict(row: ExecPositionRow) -> dict:
        return {
            "id": row.id, "symbol": row.symbol, "entry_price": row.entry_price,
            "qty": row.qty, "stop_price": row.stop_price, "tp_price": row.tp_price,
            "status": row.status, "protective_order_id": row.protective_order_id,
            "pnl_quote": row.pnl_quote, "exit_price": row.exit_price, "mode": row.mode,
            "strategy": row.strategy, "opened_at": row.opened_at, "closed_at": row.closed_at,
        }

    @staticmethod
    def _pending_dict(row: PendingIntentRow) -> dict:
        return {
            "id": row.id, "symbol": row.symbol, "side": row.side,
            "quote_amount": row.quote_amount, "stop_price": row.stop_price,
            "take_profit": row.take_profit, "confidence": row.confidence,
            "reason": row.reason, "status": row.status, "mode": row.mode,
            "created_at": row.created_at, "resolved_at": row.resolved_at,
        }

"""Kalıcılık: SQLite + SQLAlchemy 2.0.

İki tablo:
  - signals        : üretilen her sinyal (geçmiş + watch değişim tespiti için)
  - analysis_runs  : her tarama turunun logu (kaç sembol, kaç sinyal, hatalar)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

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

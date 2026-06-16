"""Backtest motoru — look-ahead bias OLMADAN bar-bar simülasyon (spec §10).

İlkeler:
  - Bar t'de karar, YALNIZCA t'ye kadarki veriyle verilir: strategy.generate(df[:t+1]).
    (Tüm indikatörler nedensel/causal olduğu için bu, t'de bilinebilir bilgidir.)
  - Sinyal t'de üretilir ama işlem BİR SONRAKİ barın (t+1) AÇILIŞINDA dolar (+slippage).
    Aynı kapanışla hem karar verip hem işlem yapmak klasik look-ahead hatasıdır.
  - Çıkış: ters sinyal / stop / take-profit; stop & TP aynı bar içindeyse KÖTÜMSER
    varsayımla önce STOP dolmuş kabul edilir.
  - Her dolumda komisyon (varsayılan %0.1) uygulanır.

Bu motor live ile AYNI strategy.generate'i kullanır → backtest/live paritesi.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from src.core.models import Action
from src.core.simulate import check_bar_exit
from src.strategies.base import Strategy

log = logging.getLogger(__name__)


@dataclass
class Trade:
    side: str            # "long" / "short"
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    return_pct: float    # net getiri % (komisyon dahil)
    r_multiple: float    # net getiri / planlanan risk
    exit_reason: str     # "stop" / "tp" / "signal" / "end"


@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    bars: int
    num_trades: int
    win_rate: float
    total_return_pct: float
    avg_r: float
    max_drawdown_pct: float
    sharpe: float
    initial_equity: float
    final_equity: float
    trades: list[Trade]


def _close_trade(
    position: dict,
    exit_price: float,
    exit_time: datetime,
    reason: str,
    equity: float,
    commission: float,
) -> tuple[Trade, float]:
    """Pozisyonu kapatır; net getiriyi ve güncel sermayeyi döndürür."""
    entry = position["entry"]
    if position["side"] == "long":
        gross = exit_price / entry - 1.0
    else:  # short
        gross = entry / exit_price - 1.0

    # Komisyon: giriş + çıkış (notional üzerinden ~2 x oran).
    net = gross - 2.0 * commission
    equity *= 1.0 + net

    risk = position["stop_dist"] / entry if entry else 0.0
    r_multiple = net / risk if risk > 0 else 0.0

    trade = Trade(
        side=position["side"],
        entry_time=position["entry_time"],
        entry_price=round(entry, 2),
        exit_time=exit_time,
        exit_price=round(exit_price, 2),
        return_pct=round(net * 100.0, 4),
        r_multiple=round(r_multiple, 3),
        exit_reason=reason,
    )
    return trade, equity


def _sharpe(returns: list[float]) -> float:
    """Basit Sharpe (işlem-bazlı, yıllıklandırılmamış): ort. getiri / std."""
    if len(returns) < 2:
        return 0.0
    mean = statistics.fmean(returns)
    std = statistics.pstdev(returns)
    if std == 0:
        return 0.0
    return round(mean / std, 3)


def run_backtest(
    strategy: Strategy,
    df: pd.DataFrame,
    symbol: str,
    timeframe: str = "1h",
    commission: float = 0.001,
    slippage: float = 0.0005,
    initial_equity: float = 1000.0,
    warmup: int | None = None,
) -> BacktestResult:
    n = len(df)

    # İndikatör ısınması (warmup): ilk barlarda göstergeler güvenilir değil.
    if warmup is None:
        params = getattr(strategy, "params", {})
        warmup = (
            int(
                max(
                    params.get("ema_slow", 26),
                    params.get("rsi_period", 14),
                    params.get("atr_period", 14),
                )
            )
            + 10
        )
    warmup = max(2, min(warmup, n - 1))

    opens = df["open"]
    highs = df["high"]
    lows = df["low"]
    closes = df["close"]
    index = df.index

    equity = initial_equity
    peak = equity
    max_dd = 0.0
    position: dict | None = None
    trades: list[Trade] = []
    returns: list[float] = []

    # Bar t'de karar ver → t+1'de uygula. Bu yüzden n-1'e kadar.
    for i in range(warmup, n - 1):
        window = df.iloc[: i + 1]                 # SADECE t'ye kadarki veri (no look-ahead)
        sig = strategy.generate(window, symbol)

        nb_open = float(opens.iloc[i + 1])
        nb_high = float(highs.iloc[i + 1])
        nb_low = float(lows.iloc[i + 1])
        nb_time = index[i + 1]

        # 1) Açık pozisyonu bir sonraki barda yönet (stop/tp/ters sinyal)
        if position is not None:
            # Stop/hedef kontrolü paylaşılan yardımcıdan (kötümser: stop önce).
            exit_price, reason = check_bar_exit(
                position["side"], nb_high, nb_low, position["stop"], position["tp"]
            )
            # Ters sinyal çıkışı (backtest'e özgü): bir sonraki barın açılışında.
            if exit_price is None:
                if position["side"] == "long" and sig.action == Action.SELL:
                    exit_price, reason = nb_open * (1 - slippage), "signal"
                elif position["side"] == "short" and sig.action == Action.BUY:
                    exit_price, reason = nb_open * (1 + slippage), "signal"

            if exit_price is not None:
                trade, equity = _close_trade(
                    position, exit_price, nb_time, reason, equity, commission
                )
                trades.append(trade)
                returns.append(trade.return_pct)
                peak = max(peak, equity)
                if peak > 0:
                    max_dd = max(max_dd, (peak - equity) / peak)
                position = None

        # 2) Flat isek yeni pozisyon aç (sinyal seviyeleri varsa)
        if position is None and sig.action in (Action.BUY, Action.SELL):
            if sig.stop_loss is None or sig.take_profit is None or sig.suggested_entry is None:
                continue
            if sig.action == Action.BUY:
                entry = nb_open * (1 + slippage)
                stop_dist = abs(sig.suggested_entry - sig.stop_loss)
                tp_dist = abs(sig.take_profit - sig.suggested_entry)
                position = {
                    "side": "long",
                    "entry": entry,
                    "stop": entry - stop_dist,
                    "tp": entry + tp_dist,
                    "entry_time": nb_time,
                    "stop_dist": stop_dist,
                }
            else:  # SELL → short
                entry = nb_open * (1 - slippage)
                stop_dist = abs(sig.stop_loss - sig.suggested_entry)
                tp_dist = abs(sig.suggested_entry - sig.take_profit)
                position = {
                    "side": "short",
                    "entry": entry,
                    "stop": entry + stop_dist,
                    "tp": entry - tp_dist,
                    "entry_time": nb_time,
                    "stop_dist": stop_dist,
                }

    # Simülasyon sonunda açık pozisyon kaldıysa son kapanışta kapat.
    if position is not None and n > 0:
        trade, equity = _close_trade(
            position, float(closes.iloc[-1]), index[-1], "end", equity, commission
        )
        trades.append(trade)
        returns.append(trade.return_pct)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)

    num_trades = len(trades)
    wins = sum(1 for t in trades if t.return_pct > 0)
    win_rate = wins / num_trades if num_trades else 0.0
    avg_r = sum(t.r_multiple for t in trades) / num_trades if num_trades else 0.0

    return BacktestResult(
        symbol=symbol,
        timeframe=timeframe,
        bars=n,
        num_trades=num_trades,
        win_rate=round(win_rate * 100.0, 2),
        total_return_pct=round((equity / initial_equity - 1.0) * 100.0, 2),
        avg_r=round(avg_r, 3),
        max_drawdown_pct=round(max_dd * 100.0, 2),
        sharpe=_sharpe(returns),
        initial_equity=round(initial_equity, 2),
        final_equity=round(equity, 2),
        trades=trades,
    )

"""RiskManager testleri — saf, ağsız. Limitler + kill-switch + boyut + min-notional."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.execution.risk import RiskManager

# config.yaml execution: varsayılanları (plandaki güvenli profil).
ECFG = {
    "risk_per_trade_pct": 1.0,
    "stop_loss_pct": 5.0,
    "take_profit_pct": 10.0,
    "max_position_pct": 20,
    "max_total_exposure_pct": 40,
    "max_concurrent_positions": 2,
    "max_daily_loss_pct": 3.0,
    "min_order_usdt": 11,
    "cooldown_minutes": 60,
    "allocation_quote_cap": 0,
}


def _rm(**over):
    return RiskManager({**ECFG, **over})


def _check(rm, **over):
    base = dict(
        symbol="BTC/USDT", entry=100.0, stop=95.0,
        free_quote=1000.0, open_count=0, open_exposure=0.0, daily_pnl=0.0,
        last_trade_time=None, now=datetime(2026, 6, 22, 12, 0, tzinfo=UTC),
    )
    base.update(over)
    sym = base.pop("symbol")
    entry = base.pop("entry")
    stop = base.pop("stop")
    return rm.check_and_size(sym, entry, stop, **base)


def test_approve_and_size():
    # capital=1000, risk%1 → 10 USDT risk; stop mesafesi %5 → 200 USDT; max_position %20 → 200.
    d = _check(_rm())
    assert d.approved
    assert d.quote_amount == 200.0
    assert d.capital == 1000.0


def test_max_position_cap_binds():
    # max_position_pct 10 → tavan 100; risk-tabanlı 200'ü kırpar.
    d = _check(_rm(max_position_pct=10))
    assert d.approved and d.quote_amount == 100.0


def test_kill_switch():
    # capital 1000, max_daily_loss %3 = 30; günlük PnL -31 → RED.
    d = _check(_rm(), daily_pnl=-31.0)
    assert not d.approved and "Kill-switch" in d.reason


def test_concurrent_limit():
    d = _check(_rm(), open_count=2)
    assert not d.approved and "Eşzamanlı" in d.reason


def test_cooldown_blocks():
    now = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)
    d = _check(_rm(), now=now, last_trade_time=now - timedelta(minutes=10))
    assert not d.approved and "Cooldown" in d.reason


def test_cooldown_passed():
    now = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)
    d = _check(_rm(), now=now, last_trade_time=now - timedelta(minutes=90))
    assert d.approved


def test_exposure_full():
    # capital = free_quote 600 + exposure 400 = 1000; max_exposure %40 = 400; oda 0 → RED.
    d = _check(_rm(), free_quote=600.0, open_exposure=400.0)
    assert not d.approved and "Maruziyet" in d.reason


def test_min_order_floor():
    # Küçük sermaye → boyut min_order_usdt altında → RED.
    d = _check(_rm(), free_quote=20.0)
    assert not d.approved and "min_order_usdt" in d.reason


def test_invalid_stop():
    d = _check(_rm(), entry=100.0, stop=105.0)  # stop > entry (long için geçersiz)
    assert not d.approved and "stop" in d.reason.lower()


def test_allocation_cap_fixes_capital():
    # cap=500 → sermaye serbest bakiyeden bağımsız 500.
    rm = _rm(allocation_quote_cap=500)
    d = _check(rm, free_quote=100000.0)
    assert d.approved and d.capital == 500.0
    # risk %1 → 5 USDT; stop %5 → 100 USDT; max_position %20 of 500 = 100 → 100.
    assert d.quote_amount == 100.0

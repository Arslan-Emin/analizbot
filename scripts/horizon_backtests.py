"""Çok-ufuklu karşılaştırmalı backtest: bot vs gerçek (al-tut / buy & hold).

Dört zaman ufku × birden çok sembol × strateji × rejim(açık/kapalı) için backtest
çalıştırır ve her satırda BOTUN sonucunu GERÇEK piyasa hareketiyle (al-tut getirisi)
karşılaştırır. "Bot al-tut'u geçti mi?" sorusunu dürüstçe yanıtlar.

Çalıştır:  .venv\\Scripts\\python.exe -m scripts.horizon_backtests
(Ağ gerektirir; birkaç dakika sürebilir. Sonuçlar geçmiştir, gelecek garantisi değildir.)
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rich.console import Console
from rich.table import Table

from src.backtest.runner import run_backtest
from src.config import load_config, strategy_params
from src.core.regime import make_backtest_regime_fn
from src.data.market_registry import get_provider
from src.strategies.regime_filtered import RegimeFilteredStrategy
from src.strategies.registry import build_strategy

# Genişlik sabit → arka planda (tty yok) sütunlar kırpılmaz.
console = Console(width=140)

SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
# (etiket, timeframe, gün) — ufuklar
HORIZONS = [
    ("Scalping", "5m", 10),
    ("Kisa donem", "1h", 90),
    ("Orta donem", "4h", 270),
    ("Uzun donem", "1d", 900),
]
STRATEGIES = ["ema_rsi", "confluence"]
INITIAL = 1000.0


def buy_hold_pct(df) -> float:
    """Gerçek piyasa: ilk bardan son bara al-tut getirisi %."""
    if len(df) < 2:
        return 0.0
    return round((float(df["close"].iloc[-1]) / float(df["close"].iloc[0]) - 1.0) * 100.0, 2)


def main() -> None:
    cfg = load_config()
    s = cfg.settings
    now = datetime.now(UTC)
    until_ms = int(now.timestamp() * 1000)
    provider = get_provider("BTC/USDT", api_key=s.binance_api_key, api_secret=s.binance_api_secret)

    rcfg = {**dict(cfg.yaml.get("regime", {})), "mode": "gate"}

    # Rejim için benchmark (BTC günlük) bir kez — tüm ufuklarda yeniden kullanılır.
    console.print("[dim]Benchmark (BTC 1d) ve veriler çekiliyor...[/]")
    bench_since = int((now - timedelta(days=1200)).timestamp() * 1000)
    bench_daily = provider.fetch_ohlcv_range("BTC/USDT", "1d", bench_since, until_ms)
    regime_fn = make_backtest_regime_fn(bench_daily, rcfg) if not bench_daily.empty else None

    data_cache: dict = {}

    def get_data(symbol: str, tf: str, days: int):
        key = (symbol, tf, days)
        if key not in data_cache:
            since = int((now - timedelta(days=days)).timestamp() * 1000)
            data_cache[key] = provider.fetch_ohlcv_range(symbol, tf, since, until_ms)
        return data_cache[key]

    all_rows = []
    beat = {"toplam": 0, "gecti": 0}
    regime_cmp = {"acik_iyi": 0, "kapali_iyi": 0, "esit": 0}

    for label, tf, days in HORIZONS:
        table = Table(
            title=f"{label}  ({tf}, son {days} gun)  —  bot vs gercek (al-tut)",
            header_style="bold magenta",
        )
        for col in ("Sembol", "Strateji", "Rejim", "Islem", "Kazan%",
                    "Bot%", "AlTut%", "Fark%", "MaxDD%", "Sharpe"):
            table.add_column(col, justify="left" if col in ("Sembol", "Strateji", "Rejim") else "right")

        for symbol in SYMBOLS:
            df = get_data(symbol, tf, days)
            if df.empty or len(df) < 50:
                continue
            bh = buy_hold_pct(df)
            per_symbol_regime = {}  # (strateji) -> {False: getiri, True: getiri}
            for strat_name in STRATEGIES:
                params = strategy_params(cfg.yaml, strat_name)
                params["timeframe"] = tf
                per_symbol_regime[strat_name] = {}
                for use_regime in (False, True):
                    strat = build_strategy(strat_name, params)
                    if use_regime:
                        if regime_fn is None:
                            continue
                        strat = RegimeFilteredStrategy(strat, regime_fn, rcfg)
                    res = run_backtest(
                        strat, df, symbol, timeframe=tf, initial_equity=INITIAL
                    )
                    diff = round(res.total_return_pct - bh, 2)
                    per_symbol_regime[strat_name][use_regime] = res.total_return_pct
                    beat["toplam"] += 1
                    if res.total_return_pct > bh:
                        beat["gecti"] += 1
                    diff_color = "green" if diff >= 0 else "red"
                    table.add_row(
                        symbol, strat_name, "acik" if use_regime else "kapali",
                        str(res.num_trades), f"%{res.win_rate}",
                        f"%{res.total_return_pct}", f"%{bh}",
                        f"[{diff_color}]{diff:+}[/]", f"%{res.max_drawdown_pct}",
                        str(res.sharpe),
                    )
                    all_rows.append((label, symbol, strat_name, use_regime, res, bh))
                # rejim açık/kapalı karşılaştırması
                rr = per_symbol_regime[strat_name]
                if True in rr and False in rr:
                    if rr[True] > rr[False]:
                        regime_cmp["acik_iyi"] += 1
                    elif rr[True] < rr[False]:
                        regime_cmp["kapali_iyi"] += 1
                    else:
                        regime_cmp["esit"] += 1
        console.print(table)
        console.print("")

    # Özet
    console.print("[bold]==== ÖZET ====[/]")
    pct = (100.0 * beat["gecti"] / beat["toplam"]) if beat["toplam"] else 0.0
    console.print(
        f"Bot al-tut'u geçti: {beat['gecti']}/{beat['toplam']} senaryo (%{pct:.0f})."
    )
    console.print(
        f"Rejim filtresi karşılaştırması — açık daha iyi: {regime_cmp['acik_iyi']}, "
        f"kapalı daha iyi: {regime_cmp['kapali_iyi']}, eşit: {regime_cmp['esit']}."
    )
    console.print(
        "[dim italic]Sonuçlar geçmişe dayalıdır; komisyon+slippage dahildir; "
        "gelecek garantisi değildir. Yatırım tavsiyesi değildir.[/]"
    )

    # Temiz arşiv: CSV.
    out_dir = Path("out")
    out_dir.mkdir(exist_ok=True)
    csv_path = out_dir / "horizon_backtests.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "ufuk", "sembol", "strateji", "rejim", "islem", "kazanma_pct",
            "bot_getiri_pct", "altut_pct", "fark_pct", "max_dd_pct", "sharpe",
        ])
        for label, symbol, strat_name, use_regime, res, bh in all_rows:
            w.writerow([
                label, symbol, strat_name, "acik" if use_regime else "kapali",
                res.num_trades, res.win_rate, res.total_return_pct, bh,
                round(res.total_return_pct - bh, 2), res.max_drawdown_pct, res.sharpe,
            ])
    console.print(f"[dim]CSV: {csv_path}[/]")


if __name__ == "__main__":
    main()

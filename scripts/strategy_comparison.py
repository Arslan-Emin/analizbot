"""Tüm stratejileri karşılaştırır (ema_rsi / confluence / ml / ensemble) + en iyisini seçer.

ADİL KIYAS kuralları:
  - ml ve ensemble için modeller, TEST penceresinden ÖNCEKİ veride eğitilir → look-ahead yok.
  - Modeller `out/bt_models/` altına yazılır (kullanıcının `models/` klasörüne DOKUNULMAZ).
  - Tüm stratejiler **rejim filtresi AÇIK** (önceki koşunun en iyi ayarı) ile koşturulur.
  - Her satır botun getirisini GERÇEK piyasa (al-tut) ile karşılaştırır; komisyon+slippage dahil.

Karar: stratejiler tüm ufuklarda (ve scalping hariç) toplulaştırılıp sıralanır.

Çalıştır:  .venv\\Scripts\\python.exe -m scripts.strategy_comparison
"""

from __future__ import annotations

import csv
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rich.console import Console
from rich.table import Table

from src.backtest.runner import run_backtest
from src.config import load_config, strategy_params
from src.core.regime import make_backtest_regime_fn
from src.data.market_registry import get_provider
from src.ml.train import model_path, save_bundle, train_model
from src.strategies.regime_filtered import RegimeFilteredStrategy
from src.strategies.registry import build_strategy

console = Console(width=140)

SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
# (etiket, tf, test_gün, train_gün)  — train penceresi test'ten ÖNCE gelir
HORIZONS = [
    ("Scalping", "5m", 10, 25),
    ("Kisa donem", "1h", 90, 150),
    ("Orta donem", "4h", 270, 400),
    ("Uzun donem", "1d", 900, 1000),
]
STRATEGIES = ["ema_rsi", "confluence", "ml", "ensemble"]
MODEL_DIR = "out/bt_models"
INITIAL = 1000.0


def buy_hold_pct(df) -> float:
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

    console.print("[dim]Benchmark (BTC 1d) + veriler çekiliyor, modeller eğitiliyor (birkaç dk)...[/]")
    bench_since = int((now - timedelta(days=1300)).timestamp() * 1000)
    bench_daily = provider.fetch_ohlcv_range("BTC/USDT", "1d", bench_since, until_ms)
    regime_fn = make_backtest_regime_fn(bench_daily, rcfg) if not bench_daily.empty else None

    def ms(dt: datetime) -> int:
        return int(dt.timestamp() * 1000)

    def ensure_model(symbol: str, tf: str, train_df) -> bool:
        """(symbol, tf) için rf modelini ÖN veride eğitir (cache). Başarı → True."""
        path = model_path(MODEL_DIR, symbol, tf)
        if path.exists():
            return True
        if train_df is None or len(train_df) < 150:
            return False
        params = strategy_params(cfg.yaml, "ml")
        params.update({"timeframe": tf, "model_type": "rf", "model_dir": MODEL_DIR,
                       "n_estimators": 120, "cv_splits": 3, "tune": False, "calibrate": False})
        try:
            save_bundle(train_model(train_df, params), path)
            return True
        except Exception as exc:
            console.print(f"[yellow]Model eğitilemedi {symbol} {tf}: {exc}[/]")
            return False

    rows = []  # (ufuk, symbol, strat, res, bh, scalping?)
    for label, tf, test_days, train_days in HORIZONS:
        is_scalp = label == "Scalping"
        table = Table(title=f"{label} ({tf})  —  tüm stratejiler (rejim açık), bot vs al-tut",
                      header_style="bold magenta")
        for col in ("Sembol", "Strateji", "Islem", "Kazan%", "Bot%", "AlTut%", "Fark%", "MaxDD%", "Sharpe"):
            table.add_column(col, justify="left" if col in ("Sembol", "Strateji") else "right")

        for symbol in SYMBOLS:
            test_since = now - timedelta(days=test_days)
            train_since = test_since - timedelta(days=train_days)
            train_df = provider.fetch_ohlcv_range(symbol, tf, ms(train_since), ms(test_since))
            test_df = provider.fetch_ohlcv_range(symbol, tf, ms(test_since), until_ms)
            if test_df.empty or len(test_df) < 50:
                continue
            bh = buy_hold_pct(test_df)
            has_model = ensure_model(symbol, tf, train_df)

            for strat_name in STRATEGIES:
                params = strategy_params(cfg.yaml, strat_name)
                params["timeframe"] = tf
                if strat_name in ("ml", "ensemble"):
                    params["model_dir"] = MODEL_DIR
                strat = build_strategy(strat_name, params)
                if regime_fn is not None:
                    strat = RegimeFilteredStrategy(strat, regime_fn, rcfg)
                res = run_backtest(strat, test_df, symbol, timeframe=tf, initial_equity=INITIAL)
                note = "" if (has_model or strat_name not in ("ml", "ensemble")) else " (model yok)"
                diff = round(res.total_return_pct - bh, 2)
                dc = "green" if diff >= 0 else "red"
                table.add_row(
                    symbol, strat_name + note, str(res.num_trades), f"%{res.win_rate}",
                    f"%{res.total_return_pct}", f"%{bh}", f"[{dc}]{diff:+}[/]",
                    f"%{res.max_drawdown_pct}", str(res.sharpe),
                )
                rows.append((label, symbol, strat_name, res, bh, is_scalp))
        console.print(table)
        console.print("")

    _decide(rows)
    _dump_csv(rows)


def _agg(rows, strat):
    sub = [(res, bh) for (_, _, sname, res, bh, _) in rows if sname == strat]
    if not sub:
        return None
    diffs = [res.total_return_pct - bh for res, bh in sub]
    return {
        "n": len(sub),
        "beat": sum(1 for res, bh in sub if res.total_return_pct > bh),
        "mean_ret": round(statistics.fmean([res.total_return_pct for res, _ in sub]), 1),
        "mean_edge": round(statistics.fmean(diffs), 1),
        "mean_sharpe": round(statistics.fmean([res.sharpe for res, _ in sub]), 3),
        "mean_win": round(statistics.fmean([res.win_rate for res, _ in sub]), 1),
    }


def _decide(rows) -> None:
    console.print("[bold]==== STRATEJİ KARŞILAŞTIRMASI ====[/]")
    for title, subset in (("Tüm ufuklar", rows),
                          ("Scalping HARİÇ (1h/4h/1d)", [r for r in rows if not r[5]])):
        table = Table(title=title, header_style="bold cyan")
        for col in ("Strateji", "Senaryo", "Al-tut'u gecti", "Ort.getiri%", "Ort.edge%", "Ort.Sharpe", "Ort.kazan%"):
            table.add_column(col, justify="left" if col == "Strateji" else "right")
        scored = []
        for strat in STRATEGIES:
            a = _agg(subset, strat)
            if not a:
                continue
            table.add_row(strat, str(a["n"]), f"{a['beat']}/{a['n']}", f"%{a['mean_ret']}",
                          f"{a['mean_edge']:+}", str(a["mean_sharpe"]), f"%{a['mean_win']}")
            # Bileşik skor: market'e karşı edge (ana) + Sharpe (risk-ayarlı) + isabet katkısı.
            score = a["mean_edge"] + a["mean_sharpe"] * 100 + a["beat"] / a["n"] * 20
            scored.append((strat, score, a))
        console.print(table)
        scored.sort(key=lambda x: x[1], reverse=True)
        best = scored[0]
        console.print(f"[bold green]>> En iyi ({title}): {best[0]}[/] "
                      f"(edge %{best[2]['mean_edge']:+}, Sharpe {best[2]['mean_sharpe']}, "
                      f"al-tut'u {best[2]['beat']}/{best[2]['n']} geçti)\n")


def _dump_csv(rows) -> None:
    out_dir = Path("out")
    out_dir.mkdir(exist_ok=True)
    path = out_dir / "strategy_comparison.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ufuk", "sembol", "strateji", "islem", "kazanma_pct", "bot_pct",
                    "altut_pct", "edge_pct", "max_dd_pct", "sharpe"])
        for label, symbol, sname, res, bh, _ in rows:
            w.writerow([label, symbol, sname, res.num_trades, res.win_rate, res.total_return_pct,
                        bh, round(res.total_return_pct - bh, 2), res.max_drawdown_pct, res.sharpe])
    console.print(f"[dim]CSV: {path}[/]")


if __name__ == "__main__":
    main()

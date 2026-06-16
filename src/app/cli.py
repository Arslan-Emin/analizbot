"""CLI giriş noktası (Typer). Komutlar: analyze, watch, backtest.

Kullanım:
  python -m src.app.cli analyze BTC/USDT
  python -m src.app.cli watch
  python -m src.app.cli backtest BTC/USDT --from 2024-01-01 --to 2024-06-30
"""

from __future__ import annotations

import logging
import sys

import typer

from src.config import load_config, strategy_params
from src.core.engine import AnalysisEngine
from src.data.market_registry import get_provider
from src.logging_setup import setup_logging
from src.notify.console import ConsoleNotifier
from src.notify.factory import build_notifier
from src.storage.db import Repository
from src.strategies.registry import build_strategy

# Windows konsolu (cp1254 vb.) Unicode karakterlerde çökmesin diye stdout/stderr'i
# UTF-8'e al; kodlanamayan karakterde çökmek yerine yer-tutucu bas (errors="replace").
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

app = typer.Typer(
    help="Kripto Analiz & Sinyal Botu — read-only karar-destek (gerçek emir göndermez).",
    no_args_is_help=True,
    add_completion=False,
)
log = logging.getLogger(__name__)


@app.callback()
def main() -> None:
    """Alt komutlar: analyze / watch / backtest / evaluate / performance."""


def _build_calibrator(cfg, repo, strategy_name):
    """config.yaml `learning` ayarlarıyla geçmişten bir güven kalibratörü kurar."""
    from src.learning.calibrator import ConfidenceCalibrator

    lcfg = cfg.yaml.get("learning", {})
    return ConfidenceCalibrator.from_history(
        repo,
        strategy=strategy_name,
        n_bins=int(lcfg.get("calibration_bins", 10)),
        min_samples=int(lcfg.get("min_samples_for_calibration", 30)),
    )


@app.command()
def analyze(
    symbol: str = typer.Argument(..., help="İşlem çifti, örn: BTC/USDT"),
    timeframe: str | None = typer.Option(
        None, "--timeframe", "-t", help="Mum periyodu (örn 1h, 4h). Boşsa config.yaml."
    ),
    limit: int = typer.Option(500, "--limit", "-l", help="Çekilecek mum sayısı."),
    strategy_name: str | None = typer.Option(
        None, "--strategy", "-S", help="Strateji: ema_rsi | confluence (boşsa config)."
    ),
    save: bool = typer.Option(True, "--save/--no-save", help="Sinyali veritabanına kaydet."),
    calibrate: bool = typer.Option(
        False, "--calibrate/--no-calibrate", help="Güveni geçmiş isabete göre kalibre et."
    ),
) -> None:
    """Tek seferlik analiz: bir sembol için gerekçeli BUY/SELL/HOLD raporu üretir."""
    cfg = load_config()
    setup_logging(cfg.settings.log_level)

    timeframe = timeframe or cfg.yaml.get("timeframe", "1h")
    strategy_name = strategy_name or cfg.yaml.get("active_strategy", "ema_rsi")
    params = strategy_params(cfg.yaml, strategy_name)
    params["timeframe"] = timeframe  # ML model dosyası timeframe'e göre seçilir

    # API anahtarı .env'den gelir (opsiyonel, read-only). Hardcode YOK.
    provider = get_provider(
        symbol,
        api_key=cfg.settings.binance_api_key,
        api_secret=cfg.settings.binance_api_secret,
    )
    strategy = build_strategy(strategy_name, params)
    repo = Repository(cfg.settings.db_url)
    calibrator = _build_calibrator(cfg, repo, strategy_name) if calibrate else None
    engine = AnalysisEngine(provider, strategy, calibrator=calibrator)

    result = engine.analyze(symbol, timeframe=timeframe, limit=limit)
    ConsoleNotifier().send_signal(result)

    if save:
        repo.save_signal(result)


@app.command()
def symbols(
    quote: str | None = typer.Option(None, "--quote", "-q", help="Karşıt para filtresi, örn: USDT"),
    search: str | None = typer.Option(None, "--search", "-s", help="Sembolde geçen metin."),
    limit: int = typer.Option(40, "--limit", "-l", help="En fazla kaç sonuç (0 = hepsi)."),
) -> None:
    """Binance'teki işlem çiftlerini listeler (filtrelenebilir)."""
    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    provider = get_provider(
        "BTC/USDT",
        api_key=cfg.settings.binance_api_key,
        api_secret=cfg.settings.binance_api_secret,
    )
    # Sadece spot çiftleri (futures sembolleri "BTC/USDT:USDT" gibi ':' içerir).
    syms = [s for s in provider.list_symbols() if ":" not in s]
    if quote:
        suffix = "/" + quote.upper()
        syms = [s for s in syms if s.endswith(suffix)]
    if search:
        needle = search.upper()
        syms = [s for s in syms if needle in s.upper()]

    total = len(syms)
    shown = syms if limit == 0 else syms[:limit]
    header = f"Toplam {total} çift bulundu"
    if limit and total > len(shown):
        header += f" (ilk {len(shown)} gösteriliyor; --limit 0 ile hepsi)"
    typer.echo(header + ":")
    typer.echo(", ".join(shown))


def _render_screen(results, top, include_hold, scanned, errors, tf, strat) -> None:
    from rich.console import Console
    from rich.table import Table

    from src.core.models import Action
    from src.notify.format import DISCLAIMER

    console = Console()

    def _table(title, items, style):
        table = Table(title=title, header_style=style)
        table.add_column("Sembol")
        table.add_column("Güven", justify="right")
        table.add_column("Fiyat", justify="right")
        for sig in items[:top]:
            table.add_row(sig.symbol, f"%{sig.confidence * 100:.0f}", str(sig.price))
        return table

    buys = sorted(
        [r for r in results if r.action == Action.BUY], key=lambda s: s.confidence, reverse=True
    )
    sells = sorted(
        [r for r in results if r.action == Action.SELL], key=lambda s: s.confidence, reverse=True
    )

    console.print(
        f"[bold]Tarama:[/] {scanned} parite · {tf} · strateji={strat} · "
        f"{len(buys)} AL, {len(sells)} SAT, {errors} hata"
    )
    if buys:
        console.print(_table("AL fırsatları (güvene göre)", buys, "bold green"))
    if sells:
        console.print(_table("SAT fırsatları (güvene göre)", sells, "bold red"))
    if include_hold:
        holds = [r for r in results if r.action == Action.HOLD]
        if holds:
            console.print(_table("HOLD", holds, "bold yellow"))
    if not buys and not sells:
        console.print("[dim]Net AL/SAT sinyali bulunamadı.[/]")
    console.print(f"[dim italic]{DISCLAIMER}[/]")


@app.command()
def screen(
    quote: str = typer.Option("USDT", "--quote", "-q", help="Karşıt para (örn USDT)."),
    timeframe: str | None = typer.Option(None, "--timeframe", "-t", help="Mum periyodu."),
    top: int = typer.Option(10, "--top", help="Her yönde kaç sonuç gösterilsin."),
    max_symbols: int = typer.Option(
        50, "--max-symbols", "-m", help="Taranacak en fazla parite (0 = hepsi; rate-limit)."
    ),
    limit: int = typer.Option(200, "--limit", "-l", help="Sembol başına mum sayısı."),
    strategy_name: str | None = typer.Option(
        None, "--strategy", "-S", help="Strateji: ema_rsi | confluence (boşsa config)."
    ),
    include_hold: bool = typer.Option(False, "--include-hold", help="HOLD'ları da listele."),
) -> None:
    """Piyasayı tarar: parite başına sinyal üretip en güçlü fırsatları sıralar."""
    from rich.progress import track

    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    timeframe = timeframe or cfg.yaml.get("timeframe", "1h")
    strategy_name = strategy_name or cfg.yaml.get("active_strategy", "ema_rsi")
    params = strategy_params(cfg.yaml, strategy_name)
    params["timeframe"] = timeframe  # ML model dosyası timeframe'e göre seçilir

    provider = get_provider(
        "BTC/USDT",
        api_key=cfg.settings.binance_api_key,
        api_secret=cfg.settings.binance_api_secret,
    )
    strategy = build_strategy(strategy_name, params)

    suffix = "/" + quote.upper()
    syms = [s for s in provider.list_symbols() if ":" not in s and s.endswith(suffix)]
    if max_symbols > 0:
        syms = syms[:max_symbols]

    results = []
    errors = 0
    # Sembol-bazlı hata izolasyonu: biri patlarsa atla, taramayı sürdür.
    for sym in track(syms, description="Taranıyor..."):
        try:
            df = provider.fetch_ohlcv(sym, timeframe=timeframe, limit=limit)
            results.append(strategy.generate(df, sym))
        except Exception as exc:
            errors += 1
            log.debug("%s atlandı: %s", sym, exc)

    _render_screen(results, top, include_hold, len(syms), errors, timeframe, strategy_name)


def _render_backtest(result) -> None:
    from rich.console import Console
    from rich.table import Table

    from src.notify.format import DISCLAIMER

    console = Console()
    table = Table(
        title=f"Backtest: {result.symbol} {result.timeframe}",
        header_style="bold magenta",
    )
    table.add_column("Metrik")
    table.add_column("Değer", justify="right")
    table.add_row("Bar sayısı", str(result.bars))
    table.add_row("İşlem sayısı", str(result.num_trades))
    table.add_row("Toplam getiri", f"%{result.total_return_pct}")
    table.add_row("Kazanma oranı", f"%{result.win_rate}")
    table.add_row("Ortalama R", str(result.avg_r))
    table.add_row("Maks. drawdown", f"%{result.max_drawdown_pct}")
    table.add_row("Sharpe (işlem-bazlı)", str(result.sharpe))
    table.add_row("Başlangıç sermaye", str(result.initial_equity))
    table.add_row("Bitiş sermaye", str(result.final_equity))
    console.print(table)
    console.print(f"[dim italic]{DISCLAIMER}[/]")


@app.command()
def backtest(
    symbol: str = typer.Argument(..., help="İşlem çifti, örn: BTC/USDT"),
    date_from: str = typer.Option(..., "--from", help="Başlangıç tarihi YYYY-MM-DD"),
    date_to: str = typer.Option(..., "--to", help="Bitiş tarihi YYYY-MM-DD"),
    timeframe: str | None = typer.Option(None, "--timeframe", "-t", help="Mum periyodu."),
    strategy_name: str | None = typer.Option(
        None, "--strategy", "-S", help="Strateji: ema_rsi | confluence | ml (boşsa config)."
    ),
    commission: float = typer.Option(0.001, "--commission", help="Komisyon oranı (0.001=%0.1)."),
    slippage: float = typer.Option(0.0005, "--slippage", help="Kayma (slippage) oranı."),
) -> None:
    """Geçmiş veride stratejiyi simüle eder; özet metrik üretir (look-ahead'siz)."""
    from datetime import UTC, datetime

    from src.backtest.runner import run_backtest

    cfg = load_config()
    setup_logging(cfg.settings.log_level)

    timeframe = timeframe or cfg.yaml.get("timeframe", "1h")
    strategy_name = strategy_name or cfg.yaml.get("active_strategy", "ema_rsi")
    params = strategy_params(cfg.yaml, strategy_name)
    params["timeframe"] = timeframe  # ml stratejisinde model dosyası timeframe'e göre seçilir
    strategy = build_strategy(strategy_name, params)

    provider = get_provider(
        symbol,
        api_key=cfg.settings.binance_api_key,
        api_secret=cfg.settings.binance_api_secret,
    )
    if not hasattr(provider, "fetch_ohlcv_range"):
        typer.echo("Bu sağlayıcı tarih-aralığı çekmeyi desteklemiyor.")
        raise typer.Exit(code=1)

    since_ms = int(datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)
    until_ms = int(datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)

    df = provider.fetch_ohlcv_range(symbol, timeframe, since_ms, until_ms)
    if df.empty or len(df) < 30:
        typer.echo("Yeterli veri yok (aralığı genişletin veya timeframe'i büyütün).")
        raise typer.Exit(code=1)

    result = run_backtest(
        strategy,
        df,
        symbol,
        timeframe=timeframe,
        commission=commission,
        slippage=slippage,
        initial_equity=float(params.get("hypothetical_capital_quote", 1000)),
    )
    _render_backtest(result)


@app.command()
def train(
    symbol: str = typer.Argument(..., help="İşlem çifti, örn: BTC/USDT"),
    date_from: str = typer.Option(..., "--from", help="Başlangıç tarihi YYYY-MM-DD"),
    date_to: str = typer.Option(..., "--to", help="Bitiş tarihi YYYY-MM-DD"),
    timeframe: str | None = typer.Option(None, "--timeframe", "-t", help="Mum periyodu."),
    model_type: str | None = typer.Option(
        None, "--model", "-M", help="Model: rf | hgb | lgbm | xgb (boşsa config)."
    ),
    tune: bool | None = typer.Option(
        None, "--tune/--no-tune", help="Hiperparametre araması (TimeSeriesSplit)."
    ),
    calibrate: bool | None = typer.Option(
        None, "--calibrate/--no-calibrate", help="Olasılık kalibrasyonu (sigmoid)."
    ),
    cv_splits: int | None = typer.Option(None, "--cv-splits", help="Walk-forward CV kat sayısı."),
) -> None:
    """ML modeli eğitir ve kaydeder (sonra: analyze/screen --strategy ml)."""
    from datetime import UTC, datetime

    from rich.console import Console
    from rich.table import Table

    from src.ml.train import model_path, save_bundle, train_model

    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    timeframe = timeframe or cfg.yaml.get("timeframe", "1h")
    params = strategy_params(cfg.yaml, "ml")
    # CLI bayrakları config'i ezer (verilmişse).
    if model_type is not None:
        params["model_type"] = model_type
    if tune is not None:
        params["tune"] = tune
    if calibrate is not None:
        params["calibrate"] = calibrate
    if cv_splits is not None:
        params["cv_splits"] = cv_splits

    provider = get_provider(
        symbol,
        api_key=cfg.settings.binance_api_key,
        api_secret=cfg.settings.binance_api_secret,
    )
    if not hasattr(provider, "fetch_ohlcv_range"):
        typer.echo("Bu sağlayıcı tarih-aralığı çekmeyi desteklemiyor.")
        raise typer.Exit(code=1)

    since_ms = int(datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)
    until_ms = int(datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)
    df = provider.fetch_ohlcv_range(symbol, timeframe, since_ms, until_ms)
    if df.empty or len(df) < 120:
        typer.echo("Yeterli veri yok (aralığı genişletin veya timeframe'i büyütün).")
        raise typer.Exit(code=1)

    bundle = train_model(df, params)
    path = model_path(params.get("model_dir", "models"), symbol, timeframe)
    save_bundle(bundle, path)

    console = Console()
    table = Table(title=f"ML Eğitim: {symbol} {timeframe}", header_style="bold magenta")
    table.add_column("Metrik")
    table.add_column("Değer", justify="right")
    table.add_row("Model", str(bundle["model_type"]))
    table.add_row("Eğitim / Test örneği", f"{bundle['train_size']} / {bundle['test_size']}")
    table.add_row("Test doğruluğu", f"%{bundle['test_accuracy'] * 100:.1f}")
    table.add_row("CV doğruluğu (walk-forward)", f"%{bundle['cv_accuracy'] * 100:.1f}")
    table.add_row("CV F1 (macro)", f"{bundle['cv_f1']:.3f}")
    table.add_row("Kalibre", "evet" if bundle["calibrated"] else "hayır")
    if bundle["best_params"]:
        table.add_row("En iyi paramlar", str(bundle["best_params"]))
    table.add_row("Etiket dağılımı", str(bundle["label_counts"]))
    console.print(table)

    # Özellik önemi (ilk 10) — hangi analizler tahmine en çok katkı sağlıyor?
    fi = bundle.get("feature_importance")
    if fi:
        top = sorted(fi.items(), key=lambda kv: kv[1], reverse=True)[:10]
        fi_table = Table(title="En önemli 10 özellik", header_style="bold cyan")
        fi_table.add_column("Özellik")
        fi_table.add_column("Önem", justify="right")
        for feat, val in top:
            fi_table.add_row(feat, f"{val:.4f}")
        console.print(fi_table)

    console.print(f"[green]Model kaydedildi:[/] {path}")
    console.print("[dim]Sınıf bazlı rapor (test):[/]")
    console.print(bundle["report"])


@app.command()
def evaluate(
    horizon: int | None = typer.Option(
        None, "--horizon", "-h", help="Değerlendirme ufku (bar). Boşsa config.learning."
    ),
) -> None:
    """Açık sinyallerin sonucunu geçmiş veriyle çözer (öğrenme verisi toplar)."""
    from src.learning.evaluator import evaluate_open_signals

    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    repo = Repository(cfg.settings.db_url)

    provider = get_provider(
        "BTC/USDT",
        api_key=cfg.settings.binance_api_key,
        api_secret=cfg.settings.binance_api_secret,
    )
    if not hasattr(provider, "fetch_ohlcv_range"):
        typer.echo("Bu sağlayıcı tarih-aralığı çekmeyi desteklemiyor.")
        raise typer.Exit(code=1)

    lcfg = cfg.yaml.get("learning", {})
    horizon = horizon or int(lcfg.get("eval_horizon_bars", 48))
    stats = evaluate_open_signals(repo, provider, eval_horizon_bars=horizon)
    typer.echo(
        f"Değerlendirme bitti: {stats['resolved']} çözüldü, {stats['open']} açık, "
        f"{stats['errors']} hata (toplam {stats['pending']} bekleyen)."
    )


@app.command()
def performance(
    strategy_name: str | None = typer.Option(
        None, "--strategy", "-S", help="Sadece bu strateji (boşsa hepsi)."
    ),
    symbol: str | None = typer.Option(None, "--symbol", "-s", help="Sadece bu sembol."),
) -> None:
    """Çözülmüş sinyallerin başarı istatistiğini gösterir (isabet, R, Brier)."""
    from rich.console import Console
    from rich.table import Table

    from src.learning.stats import overall
    from src.learning.stats import performance as perf

    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    repo = Repository(cfg.settings.db_url)

    df = perf(repo, strategy=strategy_name, symbol=symbol)
    console = Console()
    if df.empty:
        console.print(
            "[yellow]Henüz çözülmüş sinyal yok.[/] Önce sinyal üretip 'evaluate' çalıştırın."
        )
        return

    table = Table(title="Strateji performansı (çözülmüş sinyaller)", header_style="bold magenta")
    text_cols = ("strategy", "symbol", "action")
    for col in (*text_cols, "n", "hit_rate", "avg_return_pct", "avg_r", "brier"):
        table.add_column(col, justify="left" if col in text_cols else "right")
    for _, row in df.iterrows():
        table.add_row(
            str(row["strategy"]), str(row["symbol"]), str(row["action"]),
            str(int(row["n"])), f"%{row['hit_rate']}", str(row["avg_return_pct"]),
            str(row["avg_r"]), str(row["brier"]),
        )
    console.print(table)

    o = overall(repo, strategy=strategy_name)
    console.print(
        f"[bold]Genel:[/] {o['n']} sinyal · isabet %{o['hit_rate']} · "
        f"ort. getiri %{o['avg_return_pct']} · Brier {o['brier']} "
        f"[dim](Brier düşük = güven iyi kalibre)[/]"
    )


@app.command()
def watch(
    once: bool = typer.Option(
        False, "--once", help="Tek tarama yapıp çık (test/cron için)."
    ),
    calibrate: bool = typer.Option(
        False, "--calibrate/--no-calibrate", help="Güveni geçmiş isabete göre kalibre et."
    ),
) -> None:
    """Watch modu: watchlist'i periyodik tarar, sinyal değişiminde bildirir."""
    from src.app.scheduler import run_watch

    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    notifier = build_notifier(cfg)
    run_watch(cfg, notifier, once=once, calibrate=calibrate)


if __name__ == "__main__":
    app()

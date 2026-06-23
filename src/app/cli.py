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

from src.app.live_strategy import inject_kelly as _inject_kelly
from src.app.live_strategy import maybe_dynamic_ensemble as _maybe_dynamic_ensemble
from src.app.live_strategy import regime_cfg as _regime_cfg
from src.app.live_strategy import resolve_regime_flag as _resolve_regime_flag
from src.app.live_strategy import wrap_live_regime as _wrap_live_regime
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
    help="Kripto Analiz & Sinyal Botu — read-only karar-destek; opsiyonel otonom işlem "
    "(watch --execute / trade; güvenli varsayılan: paper + onaylı).",
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


_REGIME_COLOR = {"RISK_ON": "bold green", "NEUTRAL": "bold yellow", "RISK_OFF": "bold red"}


def _render_regime_line(assessment) -> None:
    """analyze/screen başına tek satırlık rejim özeti."""
    from rich.console import Console

    color = _REGIME_COLOR.get(assessment.state.value, "white")
    extra = f" · breadth %{assessment.breadth_pct}" if assessment.breadth_pct is not None else ""
    ready = "" if assessment.ready else " [dim](yetersiz veri → kapılama kapalı)[/]"
    Console().print(
        f"[bold]Rejim:[/] [{color}]{assessment.state.value}[/] "
        f"(skor {assessment.score:+.2f}, maruziyet tavanı "
        f"%{assessment.exposure_ceiling * 100:.0f}{extra}){ready}"
    )


def _render_regime(assessment) -> None:
    """`regime` komutu için ayrıntılı rejim tablosu + gerekçeler."""
    from rich.console import Console
    from rich.table import Table

    from src.notify.format import DISCLAIMER

    console = Console()
    color = _REGIME_COLOR.get(assessment.state.value, "white")
    table = Table(title="Piyasa Rejimi", header_style="bold magenta")
    table.add_column("Alan")
    table.add_column("Değer", justify="right")
    table.add_row("Durum", f"[{color}]{assessment.state.value}[/]")
    table.add_row("Skor", f"{assessment.score:+.2f}  (-1 ayı .. +1 boğa)")
    table.add_row("Pozisyon eğilimi", assessment.position_bias)
    table.add_row("Maruziyet tavanı", f"%{assessment.exposure_ceiling * 100:.0f}")
    if assessment.breadth_pct is not None:
        table.add_row("Breadth", f"%{assessment.breadth_pct} (MA üstündeki sembol oranı)")
    if not assessment.ready:
        table.add_row("Uyarı", "[yellow]Yeterli veri yok → kapılama devre dışı[/]")
    console.print(table)
    console.print("[bold]Gerekçeler:[/]")
    for reason in assessment.reasons:
        console.print(f"  • {reason}")
    console.print(f"[dim italic]{DISCLAIMER}[/]")


def _render_derivatives(snap: dict) -> None:
    """analyze için funding rate + open interest özeti (boşsa sessiz)."""
    if not snap or ("funding_rate" not in snap and "open_interest" not in snap):
        return
    from rich.console import Console

    console = Console()
    console.print("[bold]Türev verisi (perpetual pozisyonlanma):[/]")
    if "funding_rate" in snap:
        label, note = snap.get("funding_sentiment", ("", ""))
        console.print(
            f"  • Funding %{snap['funding_rate'] * 100:.4f}/8s "
            f"[bold]{label}[/] — {note}"
        )
    if "open_interest" in snap:
        trend = snap.get("oi_trend")
        suffix = f" · eğilim: {trend[0]} (%{trend[1]:+})" if trend else ""
        console.print(f"  • Open Interest: {snap['open_interest']:,.0f}{suffix}")


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
    regime_opt: bool | None = typer.Option(
        None, "--regime/--no-regime", help="Piyasa rejimi filtresi (boşsa config.regime.enable)."
    ),
) -> None:
    """Tek seferlik analiz: bir sembol için gerekçeli BUY/SELL/HOLD raporu üretir."""
    cfg = load_config()
    setup_logging(cfg.settings.log_level)

    timeframe = timeframe or cfg.yaml.get("timeframe", "1h")
    strategy_name = strategy_name or cfg.yaml.get("active_strategy", "ema_rsi")
    params = strategy_params(cfg.yaml, strategy_name)
    params["timeframe"] = timeframe  # ML model dosyası timeframe'e göre seçilir
    params = _maybe_dynamic_ensemble(strategy_name, params, cfg.settings.db_url)
    params = _inject_kelly(strategy_name, symbol, params, cfg.settings.db_url)

    # API anahtarı .env'den gelir (opsiyonel). analyze SADECE okur; emir göndermez. Hardcode YOK.
    provider = get_provider(
        symbol,
        api_key=cfg.settings.binance_api_key,
        api_secret=cfg.settings.binance_api_secret,
    )
    strategy = build_strategy(strategy_name, params)

    # Opsiyonel piyasa rejimi filtresi (karşı-rejim sinyalini zayıflat/ele).
    rcfg = _regime_cfg(cfg)
    assessment = None
    if _resolve_regime_flag(regime_opt, rcfg):
        strategy, assessment = _wrap_live_regime(strategy, provider, rcfg)

    repo = Repository(cfg.settings.db_url)
    calibrator = _build_calibrator(cfg, repo, strategy_name) if calibrate else None
    engine = AnalysisEngine(provider, strategy, calibrator=calibrator)

    result = engine.analyze(symbol, timeframe=timeframe, limit=limit)
    if assessment is not None:
        _render_regime_line(assessment)
    ConsoleNotifier().send_signal(result)

    # Opsiyonel türev verisi (funding/OI) raporu — config.data ile açılır.
    dcfg = dict(cfg.yaml.get("data", {}))
    if dcfg.get("use_funding", False) or dcfg.get("use_open_interest", False):
        from src.core.derivatives import derivatives_snapshot

        _render_derivatives(derivatives_snapshot(provider, symbol, dcfg))

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
    regime_opt: bool | None = typer.Option(
        None, "--regime/--no-regime", help="Piyasa rejimi filtresi (boşsa config.regime.enable)."
    ),
) -> None:
    """Piyasayı tarar: parite başına sinyal üretip en güçlü fırsatları sıralar."""
    from rich.progress import track

    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    timeframe = timeframe or cfg.yaml.get("timeframe", "1h")
    strategy_name = strategy_name or cfg.yaml.get("active_strategy", "ema_rsi")
    params = strategy_params(cfg.yaml, strategy_name)
    params["timeframe"] = timeframe  # ML model dosyası timeframe'e göre seçilir
    params = _maybe_dynamic_ensemble(strategy_name, params, cfg.settings.db_url)

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

    # Opsiyonel piyasa rejimi filtresi. Breadth için taranan ilk N sembolü kullan.
    rcfg = _regime_cfg(cfg)
    assessment = None
    if _resolve_regime_flag(regime_opt, rcfg):
        breadth_syms = syms[: int(rcfg.get("breadth_top_n", 30))] or None
        strategy, assessment = _wrap_live_regime(strategy, provider, rcfg, symbols=breadth_syms)
        _render_regime_line(assessment)

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


@app.command()
def regime(
    quote: str = typer.Option("USDT", "--quote", "-q", help="Breadth için karşıt para."),
    breadth_top: int | None = typer.Option(
        None, "--breadth-top", help="Breadth'te kaç sembol (boşsa config)."
    ),
    no_breadth: bool = typer.Option(
        False, "--no-breadth", help="Breadth'i atla (sadece benchmark trendi)."
    ),
) -> None:
    """Piyasa rejimini gösterir: RISK_ON/NEUTRAL/RISK_OFF + benchmark trendi + breadth."""
    from src.core.regime import build_live_regime, select_breadth_symbols

    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    rcfg = _regime_cfg(cfg)
    provider = get_provider(
        "BTC/USDT",
        api_key=cfg.settings.binance_api_key,
        api_secret=cfg.settings.binance_api_secret,
    )

    symbols = None
    if not no_breadth and bool(rcfg.get("use_breadth", True)):
        n = breadth_top or int(rcfg.get("breadth_top_n", 30))
        symbols = select_breadth_symbols(provider, quote, n)
    assessment = build_live_regime(provider, rcfg, symbols)
    _render_regime(assessment)


def _render_pairs(r) -> None:
    from rich.console import Console
    from rich.table import Table

    from src.notify.format import DISCLAIMER

    console = Console()
    color = {"LONG_SPREAD": "bold green", "SHORT_SPREAD": "bold red", "FLAT": "bold yellow"}.get(
        r.signal, "white"
    )
    table = Table(title=f"Pair Trade: {r.symbol_a} ~ {r.symbol_b}", header_style="bold magenta")
    table.add_column("Alan")
    table.add_column("Değer", justify="right")
    table.add_row("Sinyal", f"[{color}]{r.signal}[/]")
    coint_note = "anlamlı" if r.cointegrated else "zayıf"
    table.add_row("Cointegration p", f"{r.coint_pvalue} ({coint_note})")
    table.add_row("Hedge oranı β", str(r.hedge_ratio))
    table.add_row("Spread z-skoru", f"{r.zscore:+}")
    if r.half_life is not None:
        table.add_row("Yarı-ömür (bar)", str(r.half_life))
    table.add_row("Örtüşen bar", str(r.n))
    console.print(table)
    console.print("[bold]Gerekçeler:[/]")
    for reason in r.reasons:
        console.print(f"  • {reason}")
    console.print(f"[dim italic]{DISCLAIMER}[/]")


@app.command()
def pairs(
    symbol_a: str = typer.Argument(..., help="Birinci sembol, örn: BTC/USDT"),
    symbol_b: str = typer.Argument(..., help="İkinci sembol, örn: ETH/USDT"),
    timeframe: str | None = typer.Option(None, "--timeframe", "-t", help="Mum periyodu."),
    limit: int = typer.Option(500, "--limit", "-l", help="Çekilecek mum sayısı."),
) -> None:
    """İki sembol için cointegration tabanlı pair-trade analizi (istatistiksel arbitraj)."""
    from src.strategies.pairs import analyze_pair

    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    timeframe = timeframe or cfg.yaml.get("timeframe", "1h")
    pcfg = dict(cfg.yaml.get("pairs", {}))

    provider = get_provider(
        symbol_a,
        api_key=cfg.settings.binance_api_key,
        api_secret=cfg.settings.binance_api_secret,
    )
    df_a = provider.fetch_ohlcv(symbol_a, timeframe=timeframe, limit=limit)
    df_b = provider.fetch_ohlcv(symbol_b, timeframe=timeframe, limit=limit)
    result = analyze_pair(df_a["close"], df_b["close"], symbol_a, symbol_b, pcfg)
    _render_pairs(result)


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
    regime_opt: bool | None = typer.Option(
        None, "--regime/--no-regime", help="Piyasa rejimi filtresi (boşsa config.regime.enable)."
    ),
) -> None:
    """Geçmiş veride stratejiyi simüle eder; özet metrik üretir (look-ahead'siz).

    --regime ile/olmadan iki kez çalıştırıp metrikleri (kazanma oranı, ortalama R,
    drawdown) karşılaştırarak rejim filtresinin etkisini ölçebilirsiniz.
    """
    from datetime import UTC, datetime

    from src.backtest.runner import run_backtest

    cfg = load_config()
    setup_logging(cfg.settings.log_level)

    timeframe = timeframe or cfg.yaml.get("timeframe", "1h")
    strategy_name = strategy_name or cfg.yaml.get("active_strategy", "ema_rsi")
    params = strategy_params(cfg.yaml, strategy_name)
    params["timeframe"] = timeframe  # ml stratejisinde model dosyası timeframe'e göre seçilir
    params = _maybe_dynamic_ensemble(strategy_name, params, cfg.settings.db_url)
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

    # Opsiyonel rejim filtresi: benchmark günlük veriyi (200+ bar için tampon ile)
    # çekip her bara KAPANMIŞ son günlük rejimi uygula (look-ahead yok).
    rcfg = _regime_cfg(cfg)
    if _resolve_regime_flag(regime_opt, rcfg):
        from src.core.regime import make_backtest_regime_fn
        from src.strategies.regime_filtered import RegimeFilteredStrategy

        benchmark = str(rcfg.get("benchmark", "BTC/USDT"))
        btf = str(rcfg.get("timeframe", "1d"))
        buffer_ms = (int(rcfg.get("trend_period", 200)) + 80) * 86_400_000
        bench_df = provider.fetch_ohlcv_range(benchmark, btf, since_ms - buffer_ms, until_ms)
        if not bench_df.empty:
            # Backtest 'gate' semantiği: motor işlemi action'a göre açar (confidence'a
            # değil), bu yüzden soft mod giriş üretmez. Karşı-rejim işlemleri ELEMEK
            # için burada gate modunu zorlarız → "filtreli vs filtresiz" anlamlı olur.
            bt_rcfg = {**rcfg, "mode": "gate"}
            regime_fn = make_backtest_regime_fn(bench_df, bt_rcfg)
            strategy = RegimeFilteredStrategy(strategy, regime_fn, bt_rcfg)
            typer.echo(
                f"Rejim filtresi AÇIK (benchmark {benchmark} {btf}, gate modu: "
                f"karşı-rejim işlemler elenir)."
            )
        else:
            typer.echo("Rejim benchmark verisi alınamadı → filtresiz devam.")

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


def _render_walkforward(symbol, timeframe, strategy_name, result) -> None:
    from rich.console import Console
    from rich.table import Table

    from src.notify.format import DISCLAIMER

    console = Console()
    console.print(
        f"[bold]Walk-forward:[/] {symbol} {timeframe} · strateji={strategy_name} · "
        f"metrik={result.metric} · {result.param_grid_size} kombinasyon"
    )
    if not result.folds:
        console.print(
            "[yellow]Yeterli veri yok: train+test penceresi veriye sığmıyor. "
            "Aralığı genişletin veya --train/--test küçültün.[/]"
        )
        return

    table = Table(title="Katlar: in-sample seçim → OOS test", header_style="bold magenta")
    cols = (
        "Kat", "En iyi paramlar", "In-sample", "OOS",
        "OOS kazanma%", "OOS işlem", "OOS getiri%",
    )
    for col in cols:
        table.add_column(col, justify="left" if "param" in col else "right")
    for f in result.folds:
        table.add_row(
            str(f.fold), str(f.best_params), str(f.in_sample_metric), str(f.oos_metric),
            f"%{f.oos_win_rate}", str(f.oos_trades), f"%{f.oos_return_pct}",
        )
    console.print(table)
    console.print(
        f"[bold]OOS ortalama:[/] {result.metric}={result.oos_avg_metric} · "
        f"kazanma %{result.oos_avg_win_rate} · getiri %{result.oos_avg_return_pct} · "
        f"toplam {result.oos_total_trades} işlem  [dim](OOS = görülmemiş veri, gerçek beklenti)[/]"
    )
    rob = result.robustness
    risk = rob.get("overfit_risk")
    color = "red" if risk else "green"
    console.print(
        f"[bold]Sağlamlık:[/] [{color}]{rob.get('note', '')}[/] "
        f"(ızgara {rob.get('grid_size')}, spike_ratio {rob.get('spike_ratio', '-')}, "
        f"plato {rob.get('plateau_count', '-')})"
    )
    if risk:
        console.print(
            "[red]⚠ Overfit riski: en iyi parametre izole bir tepe noktası. "
            "In-sample 'en iyi'ye değil, OOS ortalamasına güvenin.[/]"
        )
    console.print(f"[dim italic]{DISCLAIMER}[/]")


@app.command()
def optimize(
    symbol: str = typer.Argument(..., help="İşlem çifti, örn: BTC/USDT"),
    date_from: str = typer.Option(..., "--from", help="Başlangıç tarihi YYYY-MM-DD"),
    date_to: str = typer.Option(..., "--to", help="Bitiş tarihi YYYY-MM-DD"),
    timeframe: str | None = typer.Option(None, "--timeframe", "-t", help="Mum periyodu."),
    strategy_name: str | None = typer.Option(
        None, "--strategy", "-S", help="Strateji (boşsa config.active_strategy)."
    ),
    metric: str | None = typer.Option(
        None, "--metric", help="Optimizasyon metriği: avg_r | total_return_pct | win_rate | sharpe."
    ),
    train: int | None = typer.Option(None, "--train", help="In-sample bar sayısı."),
    test: int | None = typer.Option(None, "--test", help="Out-of-sample bar sayısı."),
    step: int | None = typer.Option(None, "--step", help="Pencere kaydırma adımı (boşsa test)."),
    commission: float = typer.Option(0.001, "--commission", help="Komisyon oranı."),
    slippage: float = typer.Option(0.0005, "--slippage", help="Kayma oranı."),
) -> None:
    """Walk-forward optimizasyon: parametreyi in-sample seçer, OOS'ta test eder + sağlamlık."""
    from datetime import UTC, datetime

    from src.backtest.walkforward import grid_combos, walk_forward

    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    timeframe = timeframe or cfg.yaml.get("timeframe", "1h")
    strategy_name = strategy_name or cfg.yaml.get("active_strategy", "ema_rsi")
    base_params = strategy_params(cfg.yaml, strategy_name)
    base_params["timeframe"] = timeframe

    wcfg = dict(cfg.yaml.get("walkforward", {}))
    grid = dict(wcfg.get("grid", {}))
    if not grid:
        typer.echo("config.walkforward.grid tanımlı değil (optimize edilecek parametre yok).")
        raise typer.Exit(code=1)
    metric = metric or str(wcfg.get("metric", "avg_r"))
    train = train or int(wcfg.get("train_bars", 750))
    test = test or int(wcfg.get("test_bars", 250))

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
    if df.empty or len(df) < train + test:
        typer.echo(
            f"Yeterli veri yok ({len(df)} bar < train {train} + test {test}). "
            f"Aralığı genişletin veya --train/--test küçültün."
        )
        raise typer.Exit(code=1)

    n_combos = sum(1 for _ in grid_combos(grid))
    typer.echo(f"Walk-forward çalışıyor: {n_combos} kombinasyon × katlar (biraz sürebilir)...")
    result = walk_forward(
        strategy_name, df, grid, base_params=base_params, symbol=symbol, timeframe=timeframe,
        train_bars=train, test_bars=test, step=step, metric=metric,
        commission=commission, slippage=slippage, min_trades=int(wcfg.get("min_trades", 5)),
    )
    _render_walkforward(symbol, timeframe, strategy_name, result)


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

    # Opsiyonel: funding rate'i ML özelliği olarak ekle (look-ahead'siz hizalanır).
    dcfg = dict(cfg.yaml.get("data", {}))
    if dcfg.get("use_funding_features", False):
        from src.core.derivatives import merge_funding_history

        df = merge_funding_history(df, provider, symbol, {**params, **dcfg})
        if "funding_rate" in df.columns:
            typer.echo("Funding rate ML özelliği eklendi.")

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
    execute: bool = typer.Option(
        False, "--execute", help="OTONOM İŞLEM: emir ver (config execution.enabled şart)."
    ),
    live: bool = typer.Option(
        False, "--live", help="CANLI kilit (gerçek para; +LIVE_TRADING=1 +mode: live)."
    ),
) -> None:
    """Watch modu: watchlist'i periyodik tarar, sinyal değişiminde bildirir.

    --execute olmadan READ-ONLY'dir (sadece sinyal/bildirim). --execute ile config
    `execution:` ayarlarına göre paper/testnet/live emir verir; canlı için ÜÇLÜ KİLİT
    (LIVE_TRADING=1 + execution.mode: live + --live) gerekir.
    """
    from src.app.scheduler import run_watch

    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    notifier = build_notifier(cfg)
    run_watch(cfg, notifier, once=once, calibrate=calibrate, execute=execute, live=live)


@app.command()
def telegram(
    live: bool = typer.Option(False, "--live", help="Canlı kilit (gerçek para)."),
) -> None:
    """Telegram kontrol botu: uzaktan /status /approve /close /panic + otomatik tarama/işlem.

    Tek süreçte hem periyodik tarar+emir verir (watch --execute gibi) hem de telefondan
    komut kabul eder. .env'de TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID şart; yalnız o chat
    yetkilidir. Canlı için ayrıca üçlü kilit (LIVE_TRADING=1 + mode: live + --live).
    """
    from src.app.telegram_bot import run_telegram_bot

    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    run_telegram_bot(cfg, live=live)


# --------------------------------------------------------------------------- #
# Performans koçu + tez (thesis) takibi
# --------------------------------------------------------------------------- #


def _render_coach(review: dict) -> None:
    from rich.console import Console
    from rich.table import Table

    from src.notify.format import DISCLAIMER

    console = Console()
    payoff = review.get("payoff")
    console.print(
        f"[bold]Performans Koçu[/] · {review['n']} işlem · isabet %{review['win_rate']} · "
        f"avg R {review['avg_r']} · payoff {payoff if payoff is not None else '∞'} · "
        f"Brier {review['brier']}"
    )
    colors = {"OK": "green", "WARN": "yellow", "REVIEW": "red"}
    table = Table(header_style="bold magenta")
    table.add_column("Eksen")
    table.add_column("Durum")
    table.add_column("Yorum")
    for name, level, msg in review["axes"]:
        table.add_row(name, f"[{colors.get(level, 'white')}]{level}[/]", msg)
    console.print(table)
    console.print(f"[dim italic]{DISCLAIMER}[/]")


@app.command()
def coach(
    strategy_name: str | None = typer.Option(None, "--strategy", "-S", help="Sadece bu strateji."),
    symbol: str | None = typer.Option(None, "--symbol", "-s", help="Sadece bu sembol."),
) -> None:
    """Çözülmüş sinyalleri 5 eksende değerlendiren disiplin koçu (OK/WARN/REVIEW)."""
    from src.learning.coach import coach_review

    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    repo = Repository(cfg.settings.db_url)
    _render_coach(coach_review(repo.outcomes(strategy=strategy_name, symbol=symbol)))


thesis_app = typer.Typer(
    help="Tez (fikir) yaşam döngüsü: IDEA→ENTRY_READY→ACTIVE→CLOSED (+INVALIDATED).",
    no_args_is_help=True,
)
app.add_typer(thesis_app, name="thesis")

_THESIS_STATE_COLOR = {
    "IDEA": "cyan", "ENTRY_READY": "blue", "ACTIVE": "bold green",
    "CLOSED": "magenta", "INVALIDATED": "dim",
}


def _render_thesis(t: dict) -> None:
    from rich.console import Console

    console = Console()
    color = _THESIS_STATE_COLOR.get(t["state"], "white")
    console.print(
        f"#{t['id']} [bold]{t['symbol']}[/] {t['direction']} · "
        f"[{color}]{t['state']}[/] · strateji={t['strategy']}"
    )
    if t.get("thesis"):
        console.print(f"  Tez: {t['thesis']}")
    lvl = [f"giriş {t['entry_price']}", f"stop {t['stop_loss']}", f"hedef {t['take_profit']}"]
    console.print("  " + " · ".join(s for s in lvl if "None" not in s))
    if t["state"] == "CLOSED":
        console.print(
            f"  Sonuç: getiri %{t['realized_return_pct']} · "
            f"MAE %{t['mae_pct']} · MFE %{t['mfe_pct']} · çıkış {t['exit_price']}"
        )


def _render_thesis_list(rows: list[dict]) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    if not rows:
        console.print("[yellow]Kayıtlı tez yok.[/] 'thesis create ...' ile ekleyin.")
        return
    table = Table(title="Tezler", header_style="bold magenta")
    for col in ("id", "sembol", "yön", "durum", "getiri%", "MAE%", "MFE%"):
        table.add_column(col, justify="left" if col in ("sembol", "yön", "durum") else "right")
    for t in rows:
        color = _THESIS_STATE_COLOR.get(t["state"], "white")
        table.add_row(
            str(t["id"]), t["symbol"], t["direction"], f"[{color}]{t['state']}[/]",
            "" if t["realized_return_pct"] is None else f"%{t['realized_return_pct']}",
            "" if t["mae_pct"] is None else f"%{t['mae_pct']}",
            "" if t["mfe_pct"] is None else f"%{t['mfe_pct']}",
        )
    console.print(table)


@thesis_app.command("create")
def thesis_create(
    symbol: str = typer.Argument(..., help="Sembol, örn: BTC/USDT veya AAPL"),
    direction: str = typer.Option("long", "--dir", "-d", help="long | short"),
    text: str = typer.Option("", "--text", "-m", help="Tez gerekçesi (serbest metin)."),
    entry: float | None = typer.Option(None, "--entry", help="Planlanan giriş fiyatı."),
    stop: float | None = typer.Option(None, "--stop", help="Stop-loss."),
    tp: float | None = typer.Option(None, "--tp", help="Hedef (take-profit)."),
) -> None:
    """Yeni bir tez oluşturur (IDEA durumunda)."""
    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    repo = Repository(cfg.settings.db_url)
    tid = repo.create_thesis(
        symbol, direction.lower(), text, entry_price=entry, stop_loss=stop, take_profit=tp
    )
    typer.echo(f"Tez #{tid} oluşturuldu: {symbol} {direction} (IDEA).")


@thesis_app.command("list")
def thesis_list(
    state: str | None = typer.Option(None, "--state", "-s", help="Duruma göre filtrele."),
) -> None:
    """Tezleri listeler (opsiyonel duruma göre)."""
    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    repo = Repository(cfg.settings.db_url)
    _render_thesis_list(repo.list_theses(state=state.upper() if state else None))


@thesis_app.command("show")
def thesis_show(thesis_id: int = typer.Argument(..., help="Tez id'si")) -> None:
    """Tek bir tezin ayrıntısını gösterir."""
    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    repo = Repository(cfg.settings.db_url)
    t = repo.get_thesis(thesis_id)
    if t is None:
        typer.echo(f"Tez #{thesis_id} bulunamadı.")
        raise typer.Exit(code=1)
    _render_thesis(t)


@thesis_app.command("advance")
def thesis_advance(
    thesis_id: int = typer.Argument(..., help="Tez id'si"),
    to: str = typer.Option(..., "--to", help="Yeni durum: ENTRY_READY | ACTIVE | INVALIDATED ..."),
) -> None:
    """Tezi yeni bir duruma geçirir (geçiş kuralları doğrulanır)."""
    from src.learning.thesis import ThesisState, can_transition

    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    repo = Repository(cfg.settings.db_url)
    t = repo.get_thesis(thesis_id)
    if t is None:
        typer.echo(f"Tez #{thesis_id} bulunamadı.")
        raise typer.Exit(code=1)
    to_state = to.upper()
    try:
        ThesisState(to_state)
    except ValueError:
        typer.echo(f"Geçersiz durum: {to}. Geçerli: {[s.value for s in ThesisState]}")
        raise typer.Exit(code=1) from None
    if to_state == "CLOSED":
        typer.echo("Kapatmak için 'thesis close' kullanın (MAE/MFE + getiri hesaplanır).")
        raise typer.Exit(code=1)
    if not can_transition(t["state"], to_state):
        typer.echo(f"Geçersiz geçiş: {t['state']} → {to_state}.")
        raise typer.Exit(code=1)
    repo.update_thesis(thesis_id, state=to_state)
    typer.echo(f"Tez #{thesis_id}: {t['state']} → {to_state}.")


@thesis_app.command("invalidate")
def thesis_invalidate(thesis_id: int = typer.Argument(..., help="Tez id'si")) -> None:
    """Tezi INVALIDATED yapar (giriş olmadan iptal)."""
    from src.learning.thesis import can_transition

    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    repo = Repository(cfg.settings.db_url)
    t = repo.get_thesis(thesis_id)
    if t is None or not can_transition(t["state"], "INVALIDATED"):
        typer.echo("Tez bulunamadı veya bu durumdan geçersizleştirilemez.")
        raise typer.Exit(code=1)
    repo.update_thesis(thesis_id, state="INVALIDATED")
    typer.echo(f"Tez #{thesis_id} INVALIDATED.")


@thesis_app.command("close")
def thesis_close(
    thesis_id: int = typer.Argument(..., help="Tez id'si"),
    exit_price: float | None = typer.Option(None, "--exit", help="Çıkış fiyatı (boşsa canlı)."),
    timeframe: str = typer.Option("1d", "--timeframe", "-t", help="MAE/MFE için mum periyodu."),
) -> None:
    """Aktif tezi kapatır: getiri + MAE/MFE postmortem hesaplar."""
    from datetime import UTC, datetime

    from src.learning.thesis import can_transition, compute_mae_mfe, realized_return

    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    repo = Repository(cfg.settings.db_url)
    t = repo.get_thesis(thesis_id)
    if t is None or not can_transition(t["state"], "CLOSED"):
        typer.echo("Tez bulunamadı veya ACTIVE değil (yalnız ACTIVE tez kapatılır).")
        raise typer.Exit(code=1)

    provider = get_provider(
        t["symbol"],
        api_key=cfg.settings.binance_api_key,
        api_secret=cfg.settings.binance_api_secret,
    )
    exit_p = exit_price if exit_price is not None else float(provider.get_ticker(t["symbol"]))

    # created_at'ten şimdiye kadarki yolu çekip MAE/MFE hesapla (look-ahead yok: geçmiş yol).
    created = t["created_at"]
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    since_ms = int(created.timestamp() * 1000)
    until_ms = int(datetime.now(UTC).timestamp() * 1000)
    if hasattr(provider, "fetch_ohlcv_range"):
        path = provider.fetch_ohlcv_range(t["symbol"], timeframe, since_ms, until_ms)
    else:
        path = provider.fetch_ohlcv(t["symbol"], timeframe=timeframe, limit=500)

    entry = t["entry_price"]
    if entry is None and not path.empty:
        entry = float(path["close"].iloc[0])
    entry = entry or exit_p
    highs = path["high"].tolist() if not path.empty else [exit_p]
    lows = path["low"].tolist() if not path.empty else [exit_p]
    mae, mfe = compute_mae_mfe(entry, t["direction"], highs, lows)
    ret = realized_return(entry, exit_p, t["direction"])

    repo.update_thesis(
        thesis_id, state="CLOSED", closed_at=datetime.now(UTC), exit_price=round(exit_p, 4),
        realized_return_pct=ret, mae_pct=mae, mfe_pct=mfe,
    )
    typer.echo(f"Tez #{thesis_id} CLOSED.")
    _render_thesis(repo.get_thesis(thesis_id))


# --------------------------------------------------------------------------- #
# Otonom işlem (execution) — trade sub-app
# --------------------------------------------------------------------------- #

trade_app = typer.Typer(
    help="Otonom işlem: durum, pozisyon, onay (approve/reject), kapatma, panic. "
    "Kademe config execution.mode (paper/testnet/live); canlı için üçlü kilit.",
    no_args_is_help=True,
)
app.add_typer(trade_app, name="trade")


def _exec_mode(cfg) -> str:
    """Aktif execution kademesi (paper/testnet/live)."""
    return str(cfg.yaml.get("execution", {}).get("mode", "paper")).lower()


def _build_trade_manager(cfg, *, live: bool = False):
    """trade approve/close/panic için executor + ExecutionManager kurar.

    Factory üçlü kilidi uygular → canlı modda kilit eksikse LiveLockError yükselir
    (çağıran yakalar). `enabled` BURADA zorunlu değildir: kapatma/panic manuel
    güvenlik komutlarıdır, otonom şalter kapalıyken de çalışmalı.
    """
    from src.execution.factory import build_executor
    from src.execution.manager import ExecutionManager
    from src.execution.models import DecisionMode

    repo = Repository(cfg.settings.db_url)
    provider = get_provider(
        "BTC/USDT",
        api_key=cfg.settings.binance_api_key,
        api_secret=cfg.settings.binance_api_secret,
    )
    executor = build_executor(cfg, provider, repo, live_flag=live)
    ecfg = dict(cfg.yaml.get("execution", {}))
    decision = DecisionMode(str(ecfg.get("decision", "confirm")).lower())
    mgr = ExecutionManager(
        repo, executor, build_notifier(cfg), ecfg,
        decision=decision, strategy=cfg.yaml.get("active_strategy", "ema_rsi"),
    )
    return repo, mgr


@trade_app.command("status")
def trade_status() -> None:
    """Otonom işlem özeti: kademe, açık pozisyon, maruziyet, günlük PnL, kill-switch."""
    from datetime import UTC, datetime

    from rich.console import Console
    from rich.table import Table

    from src.notify.format import DISCLAIMER

    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    repo = Repository(cfg.settings.db_url)
    ecfg = dict(cfg.yaml.get("execution", {}))
    mode = _exec_mode(cfg)

    open_pos = repo.list_positions("open", mode)
    exposure = repo.open_exposure(mode)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    daily = repo.get_daily_pnl(today, mode)
    pending = repo.list_pending_intents("PENDING", mode)

    console = Console()
    enabled = bool(ecfg.get("enabled", False))
    mode_color = {"paper": "cyan", "testnet": "yellow", "live": "bold red"}.get(mode, "white")
    table = Table(title="Otonom İşlem Durumu", header_style="bold magenta")
    table.add_column("Alan")
    table.add_column("Değer", justify="right")
    table.add_row("Kademe (mode)", f"[{mode_color}]{mode}[/]")
    table.add_row("Otonom şalter (enabled)", "[green]açık[/]" if enabled else "[dim]kapalı[/]")
    table.add_row("Karar modu", str(ecfg.get("decision", "confirm")))
    table.add_row(
        "Açık pozisyon",
        f"{len(open_pos)} / {ecfg.get('max_concurrent_positions', 2)}",
    )
    table.add_row("Toplam maruziyet", f"{exposure:.2f} {cfg.yaml.get('quote_currency', 'USDT')}")
    table.add_row("Bekleyen onay", str(len(pending)))

    cap = float(ecfg.get("allocation_quote_cap", 0) or 0)
    max_loss_pct = float(ecfg.get("max_daily_loss_pct", 3.0))
    if cap > 0:
        threshold = cap * max_loss_pct / 100.0
        ks = "🔴 TETİKLENDİ" if daily <= -threshold else "yeşil"
        table.add_row("Günlük PnL", f"{daily:+.2f} (kill-switch -{threshold:.2f} → {ks})")
    else:
        table.add_row("Günlük PnL", f"{daily:+.2f} (kill-switch %{max_loss_pct} serbest bakiyeye)")
    console.print(table)
    if open_pos:
        console.print("[dim]Ayrıntı için: trade positions[/]")
    console.print(f"[dim italic]{DISCLAIMER}[/]")


@trade_app.command("positions")
def trade_positions(
    show_all: bool = typer.Option(False, "--all", help="Kapanmış pozisyonları da göster."),
) -> None:
    """Açık (veya --all ile tüm) pozisyonları tablo olarak listeler."""
    from rich.console import Console
    from rich.table import Table

    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    repo = Repository(cfg.settings.db_url)
    mode = _exec_mode(cfg)
    rows = repo.list_positions(None if show_all else "open", mode)

    console = Console()
    if not rows:
        console.print("[yellow]Pozisyon yok.[/] (kademe: " + mode + ")")
        return

    # Açık pozisyonlar için anlık fiyat (read-only sağlayıcı, tembel kurulur).
    _provider = {}

    def _price(symbol: str) -> float | None:
        try:
            if "p" not in _provider:
                _provider["p"] = get_provider(
                    symbol, api_key=cfg.settings.binance_api_key,
                    api_secret=cfg.settings.binance_api_secret,
                )
            return float(_provider["p"].get_ticker(symbol))
        except Exception:
            return None

    table = Table(title=f"Pozisyonlar ({mode})", header_style="bold magenta")
    for col in ("id", "sembol", "durum", "giriş", "anlık", "adet", "stop", "hedef", "PnL%", "PnL"):
        table.add_column(col, justify="left" if col in ("sembol", "durum") else "right")
    for p in rows:
        entry = p["entry_price"]
        # Açıkta anlık fiyat; kapalıda çıkış fiyatı.
        price = _price(p["symbol"]) if p["status"] == "open" else p["exit_price"]
        if price and entry:
            pnl_pct = (price / entry - 1.0) * 100.0
            pnl_q = (price - entry) * p["qty"] if p["status"] == "open" else p["pnl_quote"]
            color = "green" if pnl_pct >= 0 else "red"
            pnl_pct_cell = f"[{color}]{pnl_pct:+.2f}[/]"
            pnl_cell = "" if pnl_q is None else f"{pnl_q:+.2f}"
        else:
            pnl_pct_cell = ""
            pnl_cell = "" if p["pnl_quote"] is None else f"{p['pnl_quote']:+.2f}"
        status_color = "green" if p["status"] == "open" else "dim"
        table.add_row(
            str(p["id"]), p["symbol"], f"[{status_color}]{p['status']}[/]",
            str(round(entry, 4)), "" if price is None else str(round(price, 4)),
            str(round(p["qty"], 8)), str(p["stop_price"]), str(p["tp_price"]),
            pnl_pct_cell, pnl_cell,
        )
    console.print(table)


@trade_app.command("pending")
def trade_pending() -> None:
    """Onay bekleyen emir niyetlerini listeler (confirm modu)."""
    from rich.console import Console
    from rich.table import Table

    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    repo = Repository(cfg.settings.db_url)
    mode = _exec_mode(cfg)
    rows = repo.list_pending_intents("PENDING", mode)

    console = Console()
    if not rows:
        console.print("[yellow]Bekleyen onay yok.[/]")
        return
    table = Table(title="Bekleyen Emir Niyetleri", header_style="bold magenta")
    for col in ("id", "sembol", "yön", "tutar", "stop", "hedef", "güven"):
        table.add_column(col, justify="left" if col in ("sembol", "yön") else "right")
    for it in rows:
        conf = "" if it["confidence"] is None else f"%{it['confidence'] * 100:.0f}"
        table.add_row(
            str(it["id"]), it["symbol"], it["side"], f"{it['quote_amount']:.2f}",
            str(it["stop_price"]), str(it["take_profit"]), conf,
        )
    console.print(table)
    console.print("[dim]Onayla: trade approve <id>  ·  Reddet: trade reject <id>[/]")


@trade_app.command("approve")
def trade_approve(
    intent_id: int = typer.Argument(..., help="Onaylanacak niyet id'si (trade pending)"),
    live: bool = typer.Option(False, "--live", help="Canlı kilit (gerçek para)."),
) -> None:
    """Bekleyen bir emir niyetini onaylar ve emri hayata geçirir."""
    from src.execution.factory import LiveLockError

    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    try:
        _, mgr = _build_trade_manager(cfg, live=live)
    except LiveLockError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from None
    ok, msg = mgr.approve_intent(intent_id)
    typer.echo(msg)
    if not ok:
        raise typer.Exit(code=1)


@trade_app.command("reject")
def trade_reject(
    intent_id: int = typer.Argument(..., help="Reddedilecek niyet id'si"),
) -> None:
    """Bekleyen bir emir niyetini reddeder (emir verilmez; kilit gerekmez)."""
    from datetime import UTC, datetime

    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    repo = Repository(cfg.settings.db_url)
    intent = repo.get_pending_intent(intent_id)
    if intent is None:
        typer.echo(f"Niyet #{intent_id} bulunamadı.")
        raise typer.Exit(code=1)
    if intent["status"] != "PENDING":
        typer.echo(f"Niyet #{intent_id} zaten {intent['status']}.")
        raise typer.Exit(code=1)
    repo.update_pending_intent(intent_id, status="REJECTED", resolved_at=datetime.now(UTC))
    typer.echo(f"Niyet #{intent_id} reddedildi.")


@trade_app.command("close")
def trade_close(
    symbol: str = typer.Argument(..., help="Kapatılacak sembol, örn: BTC/USDT"),
    live: bool = typer.Option(False, "--live", help="Canlı kilit (gerçek para)."),
) -> None:
    """Bir sembolün açık pozisyonunu market satışla kapatır."""
    from src.execution.factory import LiveLockError

    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    try:
        _, mgr = _build_trade_manager(cfg, live=live)
    except LiveLockError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from None
    ok, msg = mgr.close_symbol(symbol)
    typer.echo(msg)
    if not ok:
        raise typer.Exit(code=1)


@trade_app.command("panic")
def trade_panic(
    live: bool = typer.Option(False, "--live", help="Canlı kilit (gerçek para)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Onay sormadan uygula."),
) -> None:
    """ACİL DURDURMA: tüm açık pozisyonları kapat + bekleyen niyetleri reddet."""
    from src.execution.factory import LiveLockError

    cfg = load_config()
    setup_logging(cfg.settings.log_level)
    if not yes:
        typer.confirm(
            f"[{_exec_mode(cfg)}] TÜM açık pozisyonlar piyasadan kapatılacak. Emin misiniz?",
            abort=True,
        )
    try:
        _, mgr = _build_trade_manager(cfg, live=live)
    except LiveLockError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from None
    typer.echo(mgr.panic())


if __name__ == "__main__":
    app()

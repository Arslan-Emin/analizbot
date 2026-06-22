"""Walk-forward optimizasyon + parametre sağlamlığı (overfit tespiti).

NEDEN? Tek bir aralıkta "en iyi" parametreyi seçmek KLASİK aşırı-uyum (overfit)
tuzağıdır: o parametre geçmişe ezberlenmiş olabilir, gelecekte çöker. İki savunma:

  1. WALK-FORWARD: parametreyi yalnız IN-SAMPLE (geçmiş) pencerede seç, hemen
     ardından gelen OUT-OF-SAMPLE (görülmemiş) pencerede test et. Pencereyi
     ileri kaydır. OOS metrikleri "gerçekte ne beklenir"in dürüst tahminidir.
  2. SAĞLAMLIK HARİTASI: parametre ızgarasındaki metrik dağılımına bak. En iyi
     sonuç İZOLE bir tepe noktasıysa (komşular çok kötü) → kırılgan/overfit.
     Geniş bir "plato" varsa → sağlam (parametreye duyarsız).

İlham: tradermonty/claude-trading-skills — backtest-expert, strategy-pivot-designer.

Mevcut `run_backtest` (look-ahead'siz) yeniden kullanılır → backtest/live paritesi.
"""

from __future__ import annotations

import itertools
import logging
import statistics
from collections.abc import Iterator
from dataclasses import dataclass

import pandas as pd

from src.backtest.runner import run_backtest
from src.strategies.registry import build_strategy

log = logging.getLogger(__name__)


@dataclass
class FoldResult:
    fold: int
    train_bars: int
    test_bars: int
    best_params: dict
    in_sample_metric: float
    oos_metric: float
    oos_win_rate: float
    oos_trades: int
    oos_return_pct: float


@dataclass
class WalkForwardResult:
    metric: str
    folds: list[FoldResult]
    oos_avg_metric: float
    oos_avg_win_rate: float
    oos_avg_return_pct: float
    oos_total_trades: int
    robustness: dict
    param_grid_size: int


def grid_combos(param_grid: dict) -> Iterator[dict]:
    """Parametre ızgarasının kartezyen çarpımı → tek tek kombinasyon sözlükleri."""
    if not param_grid:
        yield {}
        return
    keys = list(param_grid)
    for values in itertools.product(*[param_grid[k] for k in keys]):
        yield dict(zip(keys, values, strict=True))


def _backtest_combo(
    strategy_name: str, base_params: dict, combo: dict, df, symbol, timeframe,
    commission: float, slippage: float,
):
    params = {**base_params, **combo, "timeframe": timeframe}
    strat = build_strategy(strategy_name, params)
    return run_backtest(
        strat, df, symbol, timeframe=timeframe, commission=commission, slippage=slippage,
        initial_equity=float(base_params.get("hypothetical_capital_quote", 1000)),
    )


def _optimize_in_sample(
    strategy_name, base_params, param_grid, df, symbol, timeframe,
    metric, commission, slippage, min_trades,
):
    """In-sample ızgara araması → (en_iyi_combo, en_iyi_metrik, [(combo, metrik, işlem)...])."""
    best_combo: dict | None = None
    best_metric = float("-inf")
    all_metrics: list[tuple[dict, float, int]] = []
    for combo in grid_combos(param_grid):
        try:
            res = _backtest_combo(
                strategy_name, base_params, combo, df, symbol, timeframe, commission, slippage
            )
        except Exception as exc:  # bir combo patlarsa atla
            log.debug("combo atlandı %s: %s", combo, exc)
            continue
        m = float(getattr(res, metric))
        all_metrics.append((combo, m, res.num_trades))
        # Yetersiz işlemli combo'lar yanıltıcı → en iyi seçiminde dışla.
        if res.num_trades >= min_trades and m > best_metric:
            best_metric, best_combo = m, combo
    return best_combo, best_metric, all_metrics


def _assess_robustness(
    values: list[float], *, spike_threshold: float = 2.0, plateau_band: float = 0.25
) -> dict:
    """Izgara metrik dağılımından sağlamlık/overfit değerlendirmesi (advisory)."""
    vals = [v for v in values if v == v]  # NaN ele
    if len(vals) < 3:
        return {"grid_size": len(vals), "overfit_risk": False, "note": "ızgara çok küçük"}
    best = max(vals)
    median = statistics.median(vals)
    std = statistics.pstdev(vals) or 1e-9
    spike_ratio = (best - median) / std
    # Plato: en iyinin |best|*band kadar yakınındaki combo sayısı (parametreye duyarsızlık).
    tol = abs(best) * plateau_band if best != 0 else plateau_band
    plateau = sum(1 for v in vals if v >= best - tol)
    overfit_risk = spike_ratio > spike_threshold and plateau <= 1
    return {
        "grid_size": len(vals),
        "best": round(best, 4),
        "median": round(median, 4),
        "std": round(std, 4),
        "spike_ratio": round(spike_ratio, 2),
        "plateau_count": plateau,
        "overfit_risk": overfit_risk,
        "note": (
            "En iyi sonuç izole tepe → KIRILGAN/overfit riski"
            if overfit_risk
            else "Sağlam plato (parametreye duyarsız)" if plateau >= 3
            else "Orta"
        ),
    }


def walk_forward(
    strategy_name: str,
    df: pd.DataFrame,
    param_grid: dict,
    *,
    base_params: dict,
    symbol: str,
    timeframe: str = "1h",
    train_bars: int = 750,
    test_bars: int = 250,
    step: int | None = None,
    metric: str = "avg_r",
    commission: float = 0.001,
    slippage: float = 0.0005,
    min_trades: int = 5,
) -> WalkForwardResult:
    """Yuvarlanan pencerede in-sample optimize → OOS test. OOS metriklerini birleştirir."""
    n = len(df)
    step = step or test_bars
    folds: list[FoldResult] = []
    i = 0
    fold_idx = 0

    while i + train_bars + test_bars <= n:
        train_df = df.iloc[i : i + train_bars]
        test_df = df.iloc[i + train_bars : i + train_bars + test_bars]

        best_combo, in_metric, _ = _optimize_in_sample(
            strategy_name, base_params, param_grid, train_df, symbol, timeframe,
            metric, commission, slippage, min_trades,
        )
        if best_combo is not None:
            oos = _backtest_combo(
                strategy_name, base_params, best_combo, test_df, symbol, timeframe,
                commission, slippage,
            )
            folds.append(
                FoldResult(
                    fold=fold_idx,
                    train_bars=len(train_df),
                    test_bars=len(test_df),
                    best_params=best_combo,
                    in_sample_metric=round(in_metric, 4),
                    oos_metric=round(float(getattr(oos, metric)), 4),
                    oos_win_rate=oos.win_rate,
                    oos_trades=oos.num_trades,
                    oos_return_pct=oos.total_return_pct,
                )
            )
        i += step
        fold_idx += 1

    # Sağlamlık: TÜM veri üzerinde tek geçişlik ızgara metriği dağılımı.
    _, _, full_metrics = _optimize_in_sample(
        strategy_name, base_params, param_grid, df, symbol, timeframe,
        metric, commission, slippage, min_trades,
    )
    robustness = _assess_robustness([m for _, m, t in full_metrics if t >= min_trades])

    oos_metrics = [f.oos_metric for f in folds]
    oos_returns = [f.oos_return_pct for f in folds]
    oos_wins = [f.oos_win_rate for f in folds]
    return WalkForwardResult(
        metric=metric,
        folds=folds,
        oos_avg_metric=round(statistics.fmean(oos_metrics), 4) if oos_metrics else 0.0,
        oos_avg_win_rate=round(statistics.fmean(oos_wins), 2) if oos_wins else 0.0,
        oos_avg_return_pct=round(statistics.fmean(oos_returns), 2) if oos_returns else 0.0,
        oos_total_trades=sum(f.oos_trades for f in folds),
        robustness=robustness,
        param_grid_size=sum(1 for _ in grid_combos(param_grid)),
    )

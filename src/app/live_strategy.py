"""Canlı strateji yardımcıları — rejim filtresi + dinamik ensemble + Kelly enjeksiyonu.

Bu fonksiyonlar hem CLI (analyze/screen/backtest) hem watch döngüsü (scheduler)
tarafından PAYLAŞILIR → tek kaynak, davranış birebir aynı. Önceden cli.py'de özeldi;
otonom işlemler de aynı rejim filtresine tabi olsun diye buraya taşındı.
"""

from __future__ import annotations

from src.storage.db import Repository


def regime_cfg(cfg) -> dict:
    """config.yaml `regime:` bölümünü (öneksiz anahtarlar) döndürür."""
    return dict(cfg.yaml.get("regime", {}))


def resolve_regime_flag(regime_opt: bool | None, rcfg: dict) -> bool:
    """CLI bayrağı verilmişse onu, yoksa config `regime.enable`'ı kullan."""
    return regime_opt if regime_opt is not None else bool(rcfg.get("enable", False))


def wrap_live_regime(strategy, provider, rcfg: dict, symbols: list[str] | None = None):
    """Stratejiyi CANLI rejimle sarar. (sarmalı_strateji, değerlendirme) döndürür."""
    from src.core.regime import build_live_regime, select_breadth_symbols, static_regime_fn
    from src.strategies.regime_filtered import RegimeFilteredStrategy

    if symbols is None and bool(rcfg.get("use_breadth", True)):
        symbols = select_breadth_symbols(
            provider,
            str(rcfg.get("breadth_quote", "USDT")),
            int(rcfg.get("breadth_top_n", 30)),
        )
    assessment = build_live_regime(provider, rcfg, symbols)
    wrapped = RegimeFilteredStrategy(strategy, static_regime_fn(assessment), rcfg)
    return wrapped, assessment


def maybe_dynamic_ensemble(strategy_name: str, params: dict, db_url: str) -> dict:
    """Ensemble + dynamic_weight açıksa üye ağırlıklarını geçmiş isabete göre ayarlar."""
    if strategy_name != "ensemble" or not params.get("dynamic_weight"):
        return params
    from src.strategies.ensemble import DEFAULT_MEMBERS, dynamic_weights_from_stats

    members = params.get("members") or DEFAULT_MEMBERS
    names = [m["name"] for m in members]
    weights = dynamic_weights_from_stats(Repository(db_url), names)
    return {**params, "members": [{"name": n, "weight": weights[n]} for n in names]}


def inject_kelly(strategy_name: str, symbol: str, params: dict, db_url: str) -> dict:
    """Boyutlama 'kelly' ise geçmiş isabet/ödülden Kelly girdilerini enjekte eder.

    Yalnız CANLI yolda çağrılır; backtest'te çağrılmaz (geçmiş istatistik
    backtest'e sızarsa look-ahead olur → kelly orada fixed_fractional'a düşer).
    """
    if params.get("sizing_method") != "kelly":
        return params
    if params.get("kelly_win_rate") is not None and params.get("kelly_payoff") is not None:
        return params  # config'te elle verilmiş
    from src.learning.stats import kelly_inputs

    win, payoff = kelly_inputs(Repository(db_url), strategy_name, symbol)
    if win is None:
        return params
    return {**params, "kelly_win_rate": win, "kelly_payoff": payoff}

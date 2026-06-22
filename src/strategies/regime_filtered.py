"""Rejim-filtreli strateji sarmalayıcısı (decorator).

Herhangi bir Strategy'yi sarar ve ürettiği sinyali piyasa rejimine göre kapılar.
AYNI sarmalayıcı hem CANLI (analyze/screen) hem BACKTEST'te kullanılır → tam parite.

  - Canlı: rejim bir kez hesaplanır (build_live_regime) ve `static_regime_fn` ile
    tüm pencerelere uygulanır.
  - Backtest: rejim her pencereden (`make_backtest_regime_fn`) yeniden hesaplanır;
    look-ahead yoktur.

Saran strateji, alt stratejinin `name`'ini KORUR (depolama/kalibrasyon geçmişi
sürekliliği için); kapılama olduğunda gerekçe `reasons`'a eklenir.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable

import pandas as pd

from src.core.models import Action, Signal
from src.core.regime import RegimeAssessment, gate_signal
from src.strategies.base import Strategy


class RegimeFilteredStrategy(Strategy):
    """Alt stratejiyi sarıp sinyali rejime göre kapılar."""

    def __init__(
        self,
        inner: Strategy,
        regime_fn: Callable[[pd.DataFrame], RegimeAssessment | None],
        regime_cfg: dict,
    ) -> None:
        self.inner = inner
        self.regime_fn = regime_fn
        self.regime_cfg = dict(regime_cfg)
        # Alt stratejinin adını ve paramlarını koru (motor/depolama bunlara bakar).
        self.name = getattr(inner, "name", "strategy")
        self.params = getattr(inner, "params", {})

    def generate(self, df: pd.DataFrame, symbol: str) -> Signal:
        sig = self.inner.generate(df, symbol)
        try:
            assessment = self.regime_fn(df)
        except Exception:  # rejim hesabı sinyali bozmasın
            assessment = None
        if assessment is None:
            return sig

        new_action, new_conf, reason = gate_signal(
            sig.action, sig.confidence, assessment, self.regime_cfg
        )
        if reason is None:
            return sig

        # 'gate' modu: karşı-rejim işlem elendi → HOLD + seviyeleri temizle.
        if new_action == Action.HOLD and sig.action != Action.HOLD:
            return dataclasses.replace(
                sig,
                action=Action.HOLD,
                confidence=new_conf,
                reasons=[*sig.reasons, reason],
                suggested_entry=sig.price,
                stop_loss=None,
                take_profit=None,
                suggested_size_quote=None,
            )
        # 'soft' modu: yalnız güveni düşür.
        return dataclasses.replace(sig, confidence=new_conf, reasons=[*sig.reasons, reason])

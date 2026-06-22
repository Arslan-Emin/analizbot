"""EnsembleStrategy — birden çok stratejiyi ağırlıklı oyla birleştiren meta-strateji.

NEDEN? Tek bir strateji belirli rejimlerde yanılır; bağımsız stratejilerin
UZLAŞISI tek tek her birinden daha sağlamdır (gürültü birbirini götürür, ortak
sinyal güçlenir). İlham: tradermonty/claude-trading-skills — edge-signal-aggregator.

Mekanizma:
  - Üyeler (ema_rsi, confluence, ml) AYNI veride çalışır → her biri bir Signal verir.
  - Yön bazlı AĞIRLIKLI toplam: her üye `ağırlık × güven` ile kendi yönüne katkı yapar.
  - En az `min_agreement` üye aynı yöndeyse ve o yön baskınsa BUY/SELL; aksi HOLD.
  - Güven = kazanan yönün ağırlıklı toplamı / toplam ağırlık (0..1).

Ağırlıklar config'ten gelir; `dynamic_weight` açıksa CLI, üyelerin GEÇMİŞ İSABETİNE
göre ağırlıkları `dynamic_weights_from_stats` ile ayarlar.

Saf & deterministik (üyeler deterministik olduğu sürece) → backtest güvenli.
"""

from __future__ import annotations

import logging

import pandas as pd

from src.core.indicators import atr
from src.core.models import Action, Signal
from src.strategies.base import Strategy
from src.strategies.confluence import ConfluenceStrategy
from src.strategies.ema_rsi import EmaRsiStrategy
from src.strategies.levels import compute_levels
from src.strategies.ml_strategy import MlStrategy

log = logging.getLogger(__name__)

# Üye strateji adı → sınıf (registry'den bağımsız → döngüsel import yok).
_MEMBER_CLASSES: dict[str, type[Strategy]] = {
    "ema_rsi": EmaRsiStrategy,
    "confluence": ConfluenceStrategy,
    "ml": MlStrategy,
}

# Varsayılan üyeler (config 'members' vermezse).
DEFAULT_MEMBERS: list[dict] = [
    {"name": "ema_rsi", "weight": 1.0},
    {"name": "confluence", "weight": 1.0},
    {"name": "ml", "weight": 1.0},
]


class EnsembleStrategy(Strategy):
    name = "ensemble"

    def __init__(self, params: dict) -> None:
        self.params = dict(params)
        self.min_agreement = int(params.get("min_agreement", 2))
        members_cfg = params.get("members") or DEFAULT_MEMBERS

        self.members: list[tuple[str, float, Strategy]] = []
        for m in members_cfg:
            name = m.get("name")
            cls = _MEMBER_CLASSES.get(name)
            if cls is None:
                log.warning("Ensemble: bilinmeyen üye %r atlandı", name)
                continue
            # Her üyeye AYNI paylaşılan param sözlüğü verilir (indikatör periyotları
            # örtüşür; ml ek anahtarları da burada bulunur).
            self.members.append((name, float(m.get("weight", 1.0)), cls(self.params)))

    def _hold(self, symbol: str, price: float, reasons: list[str]) -> Signal:
        return Signal(
            symbol=symbol,
            action=Action.HOLD,
            confidence=round(min(max(self._hold_conf, 0.0), 1.0), 2),
            price=round(price, 2),
            reasons=reasons,
            timeframe=str(self.params.get("timeframe", "1h")),
        )

    def generate(self, df: pd.DataFrame, symbol: str) -> Signal:
        price = float(df["close"].iloc[-1])
        self._hold_conf = 0.5

        votes: list[tuple[str, float, Signal]] = []
        for name, weight, strat in self.members:
            try:
                votes.append((name, weight, strat.generate(df, symbol)))
            except Exception as exc:  # bir üye patlarsa ensemble'ı bozmasın
                log.debug("Ensemble üyesi %s atlandı: %s", name, exc)

        if not votes:
            return self._hold(symbol, price, ["Ensemble: hiçbir üye sinyal üretmedi."])

        buy_w = sum(w * s.confidence for _, w, s in votes if s.action == Action.BUY)
        sell_w = sum(w * s.confidence for _, w, s in votes if s.action == Action.SELL)
        buy_n = sum(1 for _, _, s in votes if s.action == Action.BUY)
        sell_n = sum(1 for _, _, s in votes if s.action == Action.SELL)
        total_w = sum(w for _, w, _ in votes) or 1.0

        member_lines = [
            f"{name}: {s.action.value} (%{s.confidence * 100:.0f}, ağırlık {w:g})"
            for name, w, s in votes
        ]
        # HOLD güveni: yönler ne kadar dengeliyse "beklemede kal" o kadar güvenli.
        self._hold_conf = 1.0 - abs(buy_w - sell_w) / total_w

        if buy_n >= self.min_agreement and buy_w > sell_w:
            action = Action.BUY
            confidence = buy_w / total_w
        elif sell_n >= self.min_agreement and sell_w > buy_w:
            action = Action.SELL
            confidence = sell_w / total_w
        else:
            reasons = [
                f"Ensemble: yeterli uzlaşı yok "
                f"(AL {buy_n}/{len(votes)}, SAT {sell_n}/{len(votes)}, "
                f"min {self.min_agreement}).",
                *member_lines,
            ]
            return self._hold(symbol, price, reasons)

        atr_val = float(atr(df, int(self.params.get("atr_period", 14))).iloc[-1])
        entry, stop, tp, size = compute_levels(action, price, atr_val, self.params)
        reasons = [
            f"Ensemble {action.value}: ağırlıklı uzlaşı "
            f"(AL {buy_n}, SAT {sell_n} / {len(votes)} üye).",
            *member_lines,
        ]
        return Signal(
            symbol=symbol,
            action=action,
            confidence=round(min(max(confidence, 0.0), 1.0), 2),
            price=round(price, 2),
            reasons=reasons,
            suggested_entry=round(entry, 2) if entry is not None else None,
            stop_loss=stop,
            take_profit=tp,
            suggested_size_quote=size,
            timeframe=str(self.params.get("timeframe", "1h")),
        )


def dynamic_weights_from_stats(repo, member_names: list[str], *, floor: float = 0.1) -> dict:
    """Üye stratejilerin GEÇMİŞ İSABETİNE göre ağırlık döndürür (hit_rate/100).

    Geçmişi olmayan (n=0) üye nötr 1.0 alır; isabeti olan üye `max(floor, hit_rate)`
    ağırlık alır. Böylece geçmişte daha isabetli stratejiler oylamada baskın olur.
    """
    from src.learning.stats import overall

    weights: dict[str, float] = {}
    for name in member_names:
        o = overall(repo, strategy=name)
        if o.get("n", 0) > 0:
            weights[name] = max(floor, o.get("hit_rate", 0.0) / 100.0)
        else:
            weights[name] = 1.0
    return weights

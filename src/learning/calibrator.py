"""Güven kalibrasyonu — ham güven skorunu geçmiş ampirik isabete eşler.

Bir sinyalin güven skoru (ör. %70), gerçekte ne sıklıkla doğru çıkmış? Geçmiş
(confidence → win) çiftlerinden güven kovaları kurar, her kovanın ampirik kazanma
olasılığını **Bayesyen küçültme (shrinkage)** ile hesaplar: az örnekli kovalar genel
ortalamaya (prior) çekilir → az veriyle aşırı tepki vermez. Tamamen deterministik.
"""

from __future__ import annotations

import numpy as np

from src.storage.db import Repository

# Pseudo-count: bir kovanın örneği bu sayıya ulaşana dek prior (genel ortalama) baskın.
_PRIOR_WEIGHT = 10.0


class ConfidenceCalibrator:
    """Güven kovalarına göre ham güveni ampirik kazanma olasılığına çevirir."""

    def __init__(
        self,
        edges: np.ndarray,
        probs: np.ndarray,
        prior: float,
        n_total: int,
        ready: bool,
    ) -> None:
        self._edges = edges      # kova kenarları (n_bins+1)
        self._probs = probs      # her kova için kalibre olasılık
        self.prior = prior       # genel ampirik isabet (0-1)
        self.n_total = n_total
        self.ready = ready       # yeterli örnek var mı (min_samples)?

    @property
    def hit_rate_pct(self) -> float:
        return round(self.prior * 100.0, 2)

    @classmethod
    def from_history(
        cls,
        repo: Repository,
        strategy: str | None = None,
        *,
        n_bins: int = 10,
        min_samples: int = 30,
    ) -> ConfidenceCalibrator:
        """DB'deki çözülmüş sinyallerden bir kalibratör kurar."""
        edges = np.linspace(0.0, 1.0, n_bins + 1)
        rows = repo.outcomes(strategy=strategy)
        if not rows:
            return cls(edges, np.full(n_bins, np.nan), 0.0, 0, False)

        conf = np.array([float(r["confidence"]) for r in rows])
        win = np.array(
            [1.0 if (r["realized_return_pct"] or 0.0) > 0 else 0.0 for r in rows]
        )
        prior = float(win.mean())
        n_total = int(len(win))

        idx = np.clip(np.digitize(conf, edges[1:-1]), 0, n_bins - 1)
        probs = np.full(n_bins, prior)
        for b in range(n_bins):
            mask = idx == b
            m = int(mask.sum())
            if m == 0:
                continue  # veri yok → prior kalsın
            k = float(win[mask].sum())
            # Bayesyen shrink: az örnekte prior'a yakın, çok örnekte ampirik orana yakın.
            probs[b] = (k + _PRIOR_WEIGHT * prior) / (m + _PRIOR_WEIGHT)

        return cls(edges, probs, prior, n_total, n_total >= min_samples)

    def calibrate(self, raw_confidence: float) -> float:
        """Ham güveni kalibre olasılığa eşler. Yeterli veri yoksa ham değeri döndürür."""
        if not self.ready:
            return raw_confidence
        b = int(
            np.clip(np.digitize([raw_confidence], self._edges[1:-1])[0], 0, len(self._probs) - 1)
        )
        val = self._probs[b]
        if np.isnan(val):
            return raw_confidence
        return float(min(max(val, 0.0), 1.0))

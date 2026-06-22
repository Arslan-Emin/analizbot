"""Pozisyon boyutlama yöntemleri (saf, deterministik).

ÜÇ yöntem (config `sizing.method`):
  - fixed_fractional (varsayılan): sermayenin sabit %'ini RİSKE atar. Boyut, stop
    mesafesine göre ayarlanır → her işlemde dolar-riski sabit. (Mevcut davranış.)
  - atr_target_vol: HEDEF OYNAKLIK. Pozisyonu, varlığın ATR-oynaklığı hedefe eşit
    olacak şekilde ölçekler → düşük oynaklıkta büyür, yüksekte küçülür (vol-targeting).
  - kelly: geçmiş kazanma oranı (W) ve ödül/risk (R) ile Kelly oranı f = W − (1−W)/R.
    Pratikte YARIM Kelly + tavan kullanılır (tam Kelly çok agresiftir). Girdi yoksa
    güvenle fixed_fractional'a düşer.

TÜM çıktılar quote (örn USDT) NOTIONAL'dır ve `max_position_pct` ile sınırlanır.
NOT: Üretilen boyut ÖRNEK/EĞİTSEL'dir; gerçek emir değildir.

İlham: tradermonty/claude-trading-skills — position-sizer.
"""

from __future__ import annotations


def kelly_fraction(
    win_rate: float, payoff: float, *, half: bool = True, cap: float = 0.25
) -> float:
    """Kelly oranı: f = W − (1−W)/R. W=kazanma oranı (0..1), R=ödül/risk (avg_win/avg_loss).

    f ≤ 0 ise 0 (kenar yok → pozisyon alma). half=True → yarım Kelly. `cap` üst sınır.
    """
    if payoff <= 0 or not (0.0 <= win_rate <= 1.0):
        return 0.0
    f = win_rate - (1.0 - win_rate) / payoff
    if f <= 0:
        return 0.0
    if half:
        f *= 0.5
    return min(f, cap)


def _fixed_fractional_size(capital: float, entry: float, stop: float, params: dict) -> float | None:
    risk_pct = float(params.get("risk_per_trade_pct", 1.0))
    risk_per_unit = abs(entry - stop)
    if risk_per_unit <= 0:
        return None
    # Riske atılan sermaye / birim risk = adet; × entry = quote notional.
    return (capital * risk_pct / 100.0) / risk_per_unit * entry


def _atr_target_vol_size(
    capital: float, entry: float, atr_val: float, params: dict
) -> float | None:
    if entry <= 0 or atr_val <= 0:
        return None
    vol_pct = atr_val / entry  # bar başına ~% oynaklık
    target = float(params.get("target_vol_pct", 1.0)) / 100.0
    return capital * (target / vol_pct)


def _kelly_size(capital: float, params: dict) -> float | None:
    win = params.get("kelly_win_rate")
    payoff = params.get("kelly_payoff")
    if win is None or payoff is None:
        return None  # girdi yok → çağıran fixed_fractional'a düşer
    frac = kelly_fraction(
        float(win), float(payoff),
        half=bool(params.get("kelly_half", True)),
        cap=float(params.get("kelly_cap", 0.25)),
    )
    return capital * frac if frac > 0 else None


def _cap_size(size: float | None, capital: float, params: dict) -> float | None:
    """Portföy kısıtı: tek pozisyon sermayenin `max_position_pct`'ini aşamaz."""
    if size is None or size <= 0:
        return None
    ceiling = capital * float(params.get("max_position_pct", 100.0)) / 100.0
    return round(min(size, ceiling), 2)


def compute_size(
    method: str,
    *,
    capital: float,
    entry: float,
    stop: float,
    atr_val: float,
    params: dict,
) -> float | None:
    """Seçilen yönteme göre quote-notional pozisyon boyutu (None = boyutlanamadı)."""
    method = (method or "fixed_fractional").lower()

    if method == "kelly":
        size = _kelly_size(capital, params)
        if size is not None:
            return _cap_size(size, capital, params)
        method = "fixed_fractional"  # kelly girdisi yok → güvenli varsayılan

    if method == "atr_target_vol":
        size = _atr_target_vol_size(capital, entry, atr_val, params)
    else:  # fixed_fractional + bilinmeyen yöntemler
        size = _fixed_fractional_size(capital, entry, stop, params)

    return _cap_size(size, capital, params)

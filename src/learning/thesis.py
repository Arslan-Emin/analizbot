"""Tez (thesis) yaşam döngüsü + MAE/MFE postmortem — saf mantık.

NEDEN? Bot read-only olduğu için gerçek pozisyon tutmaz; ama kullanıcı bir fikri
(tez) fikir aşamasından kapanışa kadar TAKİP edip disiplinini ölçebilir. Bu, sinyal
üretmekten farklı bir katman: "neye, neden inandım; sonuç ne oldu; ne öğrendim".

İlham: tradermonty/claude-trading-skills — trader-memory-core (state machine).

Durum makinesi: IDEA → ENTRY_READY → ACTIVE → CLOSED (+ her aşamadan INVALIDATED).
MAE/MFE: pozisyon süresince en kötü (adverse) ve en iyi (favorable) % sapma — disiplin
ve giriş zamanlaması postmortemi için.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum


class ThesisState(str, Enum):  # noqa: UP042  (Action ile tutarlı: str+Enum, JSON/DB kolaylığı)
    IDEA = "IDEA"                # takip edilen fikir (henüz tetik yok)
    ENTRY_READY = "ENTRY_READY"  # giriş koşulları oluştu, tetik bekleniyor
    ACTIVE = "ACTIVE"            # (varsayımsal) pozisyon açık
    CLOSED = "CLOSED"            # kapandı (sonuç + MAE/MFE hesaplanır)
    INVALIDATED = "INVALIDATED"  # tez geçersizleşti (giriş olmadan iptal)


# İzin verilen geçişler. CLOSED ve INVALIDATED terminaldir.
_TRANSITIONS: dict[ThesisState, set[ThesisState]] = {
    ThesisState.IDEA: {ThesisState.ENTRY_READY, ThesisState.INVALIDATED},
    ThesisState.ENTRY_READY: {ThesisState.ACTIVE, ThesisState.INVALIDATED, ThesisState.IDEA},
    ThesisState.ACTIVE: {ThesisState.CLOSED, ThesisState.INVALIDATED},
    ThesisState.CLOSED: set(),
    ThesisState.INVALIDATED: set(),
}


def can_transition(frm: ThesisState | str, to: ThesisState | str) -> bool:
    """`frm` durumundan `to` durumuna geçiş geçerli mi?"""
    frm = ThesisState(frm)
    to = ThesisState(to)
    return to in _TRANSITIONS.get(frm, set())


def realized_return(entry: float, exit_price: float, side: str) -> float:
    """Kapanışta gerçekleşen getiri % (yön dahil). long: exit/entry-1, short: entry/exit-1."""
    if entry <= 0 or exit_price <= 0:
        return 0.0
    if side == "short":
        return round((entry / exit_price - 1.0) * 100.0, 4)
    return round((exit_price / entry - 1.0) * 100.0, 4)


def compute_mae_mfe(
    entry: float, side: str, highs: Sequence[float], lows: Sequence[float]
) -> tuple[float, float]:
    """Pozisyon süresince (MAE, MFE) yüzde olarak.

    MAE = en kötü aleyhte sapma (genelde negatif), MFE = en iyi lehte sapma (genelde pozitif).
      - long : MFE = (max(high)/entry-1)*100, MAE = (min(low)/entry-1)*100
      - short: MFE = (entry/min(low)-1)*100,  MAE = (entry/max(high)-1)*100
    Veri yoksa (0.0, 0.0).
    """
    hs = [float(h) for h in highs if h is not None]
    ls = [float(low) for low in lows if low is not None]
    if entry <= 0 or not hs or not ls:
        return 0.0, 0.0
    hi, lo = max(hs), min(ls)
    if side == "short":
        mfe = (entry / lo - 1.0) * 100.0 if lo > 0 else 0.0
        mae = (entry / hi - 1.0) * 100.0 if hi > 0 else 0.0
    else:  # long
        mfe = (hi / entry - 1.0) * 100.0
        mae = (lo / entry - 1.0) * 100.0
    return round(mae, 2), round(mfe, 2)

"""Performans koçu — çözülmüş sinyallerden 5-eksenli disiplin değerlendirmesi.

NEDEN? Ham istatistik (isabet, R) "ne" olduğunu söyler; koç "ne yapmalı"ya çevirir.
Beş eksende OK/WARN/REVIEW verdikti üretir → kullanıcı zayıf yönünü görür ve düzeltir.

İlham: tradermonty/claude-trading-skills — trade-performance-coach.

Saf fonksiyon: girdi repo.outcomes() satırları (realized_return_pct, r_multiple,
confidence, outcome, action). Ağ/DB yok → test edilebilir.
"""

from __future__ import annotations

import statistics

# Eksen eşikleri: (ad, OK eşiği, REVIEW eşiği) — yön metriğe göre koddadır.
_MIN_SAMPLES = 20


def _level(ok: bool, review: bool) -> str:
    return "REVIEW" if review else ("OK" if ok else "WARN")


def coach_review(rows: list[dict]) -> dict:
    """Çözülmüş sonuçlardan 5-eksenli koç değerlendirmesi döndürür.

    Dönüş: {n, win_rate, avg_r, payoff, brier, axes:[(ad, level, mesaj)...]}.
    level ∈ {OK, WARN, REVIEW}. Veri yoksa tek REVIEW ekseni.
    """
    n = len(rows)
    if n == 0:
        msg = "Henüz çözülmüş işlem yok — önce sinyal üretip 'evaluate' çalıştırın."
        return {
            "n": 0, "win_rate": 0.0, "avg_r": 0.0, "payoff": 0.0, "brier": 0.0,
            "axes": [("Örneklem", "REVIEW", msg)],
        }

    returns = [float(r.get("realized_return_pct") or 0.0) for r in rows]
    rmults = [float(r.get("r_multiple") or 0.0) for r in rows]
    confs = [float(r.get("confidence") or 0.0) for r in rows]
    wins = [x for x in returns if x > 0]
    losses = [x for x in returns if x <= 0]

    win_rate = len(wins) / n
    avg_r = statistics.fmean(rmults)
    avg_win = statistics.fmean(wins) if wins else 0.0
    avg_loss = statistics.fmean(losses) if losses else 0.0
    payoff = (avg_win / abs(avg_loss)) if avg_loss < 0 else float("inf")
    brier = statistics.fmean(
        [(c - (1.0 if r > 0 else 0.0)) ** 2 for c, r in zip(confs, returns, strict=True)]
    )

    axes: list[tuple[str, str, str]] = []

    # 1) Beklenti (expectancy) — işlem başına ortalama R.
    axes.append((
        "Beklenti (avg R)",
        _level(avg_r > 0.1, avg_r < 0.0),
        f"İşlem başına ortalama R = {avg_r:.2f}. "
        + ("Pozitif beklenti, iyi." if avg_r > 0.1 else
           "Negatif beklenti — strateji/parametre gözden geçir." if avg_r < 0 else
           "Sınırda; kenar (edge) zayıf."),
    ))

    # 2) Risk disiplini — kazanç/kayıp oranı (payoff).
    payoff_txt = "∞" if payoff == float("inf") else f"{payoff:.2f}"
    axes.append((
        "Risk disiplini (payoff)",
        _level(payoff >= 1.5, payoff < 1.0),
        f"Ort. kazanç/kayıp oranı = {payoff_txt}. "
        + ("Kazançlar kayıpları aşıyor." if payoff >= 1.5 else
           "Kayıplar kazançları yiyor — kâr-al/zarar-durdur dengesine bak." if payoff < 1.0 else
           "Orta; R:R hedeflerini sıkılaştır."),
    ))

    # 3) Tutarlılık — isabet oranı.
    axes.append((
        "Tutarlılık (isabet)",
        _level(win_rate > 0.45, win_rate < 0.35),
        f"İsabet oranı = %{win_rate * 100:.0f}. "
        + ("Sağlıklı." if win_rate > 0.45 else
           "Düşük — giriş seçiciliğini artır (confluence/ensemble + rejim)." if win_rate < 0.35 else
           "Kabul edilebilir ama R:R ile desteklenmeli."),
    ))

    # 4) Kalibrasyon — güven gerçeği yansıtıyor mu (Brier).
    axes.append((
        "Kalibrasyon (Brier)",
        _level(brier < 0.2, brier > 0.3),
        f"Brier = {brier:.3f} (düşük = güven iyi kalibre). "
        + ("Güven skorları gerçekçi." if brier < 0.2 else
           "Güven gerçeği yansıtmıyor — '--calibrate' kullan." if brier > 0.3 else
           "Orta; kalibrasyonu aç."),
    ))

    # 5) Örneklem yeterliliği.
    axes.append((
        "Örneklem",
        _level(n >= _MIN_SAMPLES, n < 10),
        f"{n} çözülmüş işlem. "
        + ("Yorum için yeterli." if n >= _MIN_SAMPLES else
           "Çok az — sonuçlar gürültülü, daha fazla veri topla." if n < 10 else
           "Sınırda; daha fazla örnek güveni artırır."),
    ))

    return {
        "n": n,
        "win_rate": round(win_rate * 100.0, 2),
        "avg_r": round(avg_r, 3),
        "payoff": round(payoff, 2) if payoff != float("inf") else None,
        "brier": round(brier, 4),
        "axes": axes,
    }

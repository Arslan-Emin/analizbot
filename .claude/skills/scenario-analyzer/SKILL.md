---
name: scenario-analyzer
description: Bir varlık veya piyasa için ileriye dönük Baz/Boğa/Ayı senaryoları üretir, olasılık atar ve ikinci bir eleştirel geçişle bias/kör nokta düzeltmesi yapar. Kullanıcı "ne olabilir", "senaryo analizi", "X olursa ne olur", "önümüzdeki dönem beklentisi" dediğinde kullan.
---

# scenario-analyzer

Tek bir tahmin yerine OLASILIKLI senaryolar üretir ve kendi yanlılığını denetler.
İlham: tradermonty/claude-trading-skills — scenario-analyzer (çift-ajan eleştiri).

## Yöntem (iki geçiş)
### Geçiş 1 — Senaryo üret
- **Baz (en olası)**, **Boğa (yukarı)**, **Ayı (aşağı)** senaryoları yaz.
- Her senaryo için: tetikleyiciler, etkilenecek sembol/sektörler, kabaca olasılık (%), zaman ufku.
- Girdi olarak şunları kullan: `regime-check` (mevcut rejim), `crypto-news-impact` (katalizörler),
  `crypto-analyze` (teknik durum) ve WebSearch ile güncel bağlam.

### Geçiş 2 — Eleştirel denetim (adversarial)
- İlk geçişi ELEŞTİR: Hangi varsayım zayıf? Hangi kör nokta var? Olasılıklar gerçekçi mi
  yoksa son habere mi demir atmış (anchoring)? Onay yanlılığı (confirmation bias) var mı?
- Düzeltilmiş olasılıklar + 3-5 net karar faktörü (neyi izlemeli) ile bitir.

## Çıktı
- 3 senaryo tablosu (olasılık, tetikleyici, etki) + "izlenecek sinyaller" listesi.
- Hangi rejim/haber değişiminin hangi senaryoya geçişi tetikleyeceğini belirt.

## İlkeler
- Olasılıklar toplamı ~%100 olsun; aşırı kesinlikten kaçın.
- Senaryo ≠ tahmin ≠ tavsiye. Belirsizliği açıkça koru.
- Güncel veri için WebSearch kullan ve kaynak göster.

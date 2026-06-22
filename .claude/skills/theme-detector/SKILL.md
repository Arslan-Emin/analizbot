---
name: theme-detector
description: Piyasada trend olan anlatıları/temaları (AI coinleri, L2'ler, RWA, memeler, DeFi, restaking vb.) tespit eder; hangi sektörün öne çıktığını ve hangisinin soğuduğunu değerlendirir. Kullanıcı "hangi sektör/anlatı sıcak", "trend temalar", "rotasyon nerede", "AI coinleri nasıl" dediğinde kullan. WebSearch gerektirir.
---

# theme-detector

Bireysel coin yerine ANLATI/TEMA düzeyinde bakar; sermaye rotasyonunu yakalar.
İlham: tradermonty/claude-trading-skills — theme-detector.

## Yöntem
1. **Temaları tara**: WebSearch + (varsa) `market-screen` ile sektör/anlatı performansını topla
   (örn AI, L2/L3, RWA, DeFi, meme, restaking, DePIN, oyun).
2. **Üç boyutlu skorla** her tema için:
   - **Isı (0-100)**: son performans + ilgi/hacim.
   - **Yaşam evresi**: Filizlenen → Hızlanan → Trend → Olgun → Tükenen.
   - **Güven**: Düşük/Orta/Yüksek (kaç bağımsız sinyal teyit ediyor).
3. **Sırala & bağla**: öne çıkan temalar + temsilci semboller/ETF'ler; yön (öncü/geride).

## Çıktı
- En güçlü 3-6 tema: isı, evre, güven, temsilci semboller, kısa gerekçe.
- "Olgun/Tükenen" evredeki temalarda geç-kalma riskini vurgula.

## İlkeler
- Tema sıcaklığı hızlı söner; tarih damgası ver, "geç aşama" uyarısı yap.
- Kaynak göster; sosyal medya hype'ını olgudan ayır.
- Tema ≠ tavsiye. Rejim RISK_OFF iken en sıcak tema bile temkin gerektirir.

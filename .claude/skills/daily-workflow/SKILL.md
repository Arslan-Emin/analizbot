---
name: daily-workflow
description: Günlük piyasa rutinini uçtan uca yürütür — rejim kontrolü → (risk-on ise) tarama → öne çıkanları analiz → haber etkisi → anlatısal rapor. Kullanıcı "günlük rutini çalıştır", "bugünü özetle", "sabah taraması", "her şeyi yap ve raporla" dediğinde kullan.
---

# daily-workflow

Diğer skilleri belirli bir sırayla zincirleyen orkestrasyon. İlham: tradermonty
günlük kadansı + anthropics/financial-services çok-ajan orkestrasyon deseni.

## Akış
1. **Rejim** (`regime-check`): genel havayı ölç. Bunu raporun tepesine koy.
2. **Karar kapısı**:
   - RISK_ON / NEUTRAL → adım 3'e geç (yeni fırsat ara).
   - RISK_OFF → taramayı yine yap ama "yeni alımda temkin" çerçevesiyle; SAT/koruma vurgusu.
3. **Tarama** (`market-screen` `--regime` ile): en güçlü 5-10 AL/SAT adayını çıkar.
4. **Derin analiz** (`crypto-analyze`): tarama tepesindeki 2-3 sembolü tek tek incele
   (seviyeler + funding/OI dahil).
5. **Haber** (`crypto-news-impact`): günün piyasa-hareket ettiren başlıklarını ekle.
6. **Rapor** (`narrative-report`): hepsini tek markdown rapora sentezle, istenirse `./out/`'a kaydet.

## İlkeler
- Her adımın çıktısını bir sonrakine bağlam olarak taşı (rejim → tarama yorumu → rapor).
- Adımlar başarısız olursa (ağ vb.) atla ve raporda not düş; tüm akışı durdurma.
- Tek bir nihai özet sun; ara çıktıları boğmadan en önemli 3 sonucu öne çıkar.
- Sonuç sinyal/analizdir, tavsiye değildir — uyarıyı koru.

---
name: crypto-analyze
description: Bir sembolü (kripto BTC/USDT veya hisse AAPL) analizbot ile analiz edip BUY/SELL/HOLD sinyalini, gerekçeleri, giriş/stop/hedef seviyelerini ve indikatörleri yorumla. Kullanıcı "X coini/hissesini analiz et", "BTC ne durumda", "şu an alınır mı" gibi tek-sembol analizi istediğinde kullan.
---

# crypto-analyze

analizbot CLI'sini çağırıp tek bir sembol için gerekçeli sinyal üretir ve sonucu
sade Türkçe yorumlarsın. Bot **read-only** karar-destek aracıdır; gerçek emir göndermez.

## Nasıl çalıştırılır
Proje kökünden (`C:\Users\arsla\Desktop\analizbot`) venv Python'u ile:

```
.venv\Scripts\python.exe -m src.app.cli analyze <SEMBOL> -t <TIMEFRAME> [--regime] [--calibrate]
```

- `<SEMBOL>`: kripto `BTC/USDT`, `ETH/USDT`; hisse `AAPL`, `MSFT` (otomatik doğru sağlayıcıya gider).
- `-t`: `1h`, `4h`, `1d` (hisse için `1d`/`1h`). Boşsa config.yaml varsayılanı.
- `--regime`: piyasa rejimi filtresini açar (ayı rejiminde BUY zayıflatılır/elenir). Doğruluk için önerilir.
- `--calibrate`: güveni geçmiş isabete göre kalibre eder (yeterli geçmiş varsa).
- `--no-save`: sinyali veritabanına yazma (deneme amaçlı).

## Çıktıyı nasıl yorumla
- **SİNYAL** (BUY/SELL/HOLD) + **güven %**: yüksek güven = daha çok onay koşulu sağlandı.
- **Gerekçeler**: hangi indikatörlerin tetiklediği. Kullanıcıya 2-3 maddeyle özetle.
- **Rejim satırı** (varsa): RISK_OFF'ta BUY sinyallerine temkinli yaklaş, kullanıcıyı uyar.
- **Türev verisi** (funding/OI): aşırı funding = kalabalık pozisyon = contrarian risk; belirt.
- **Giriş/Stop/Hedef/R:R**: seviyeleri aktar ama "bu yatırım tavsiyesi değildir" uyarısını koru.

## İlkeler
- Sayıları olduğu gibi aktar; uydurma. Komutu çalıştır, çıktıdan konuş.
- HOLD da geçerli bir sonuçtur ("net sinyal yok"); zorlama yorum yapma.
- Birden çok sembol istenirse her biri için ayrı çalıştır veya `market-screen` skill'ini öner.

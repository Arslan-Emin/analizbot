---
name: market-screen
description: analizbot ile piyasayı tarayıp en güçlü AL/SAT fırsatlarını güvene göre sıralar. Kullanıcı "hangi coinlerde fırsat var", "piyasayı tara", "en iyi alım adayları", "bugün ne öne çıkıyor" gibi çoklu-sembol tarama istediğinde kullan.
---

# market-screen

Bir karşıt para (örn USDT) altındaki pariteleri tarar, parite başına sinyal üretip
en güçlü fırsatları sıralar.

## Nasıl çalıştırılır
```
.venv\Scripts\python.exe -m src.app.cli screen -q USDT -m 50 -t 4h --top 10 [--regime]
```

- `-q`: karşıt para (USDT). `-m`: taranacak en fazla parite (rate-limit'e dikkat).
- `-t`: timeframe. `--top`: her yönde kaç sonuç. `--regime`: rejim filtresi (önerilir).
- `-S`: strateji (`ema_rsi` | `confluence` | `ml` | `ensemble`).

## Çıktıyı nasıl yorumla
- AL ve SAT tabloları güvene göre sıralı gelir. En üsttekiler en çok onay alanlar.
- Rejim satırı RISK_OFF ise AL fırsatlarına temkinli yaklaş; SAT/bekle eğilimini vurgula.
- Kullanıcıya ilk 3-5 adayı özetle; "daha derin bak" istenirse `crypto-analyze` ile tek tek incele.

## İlkeler
- Tarama maliyetlidir; `-m` değerini makul tut (50-100). Çok sayıda istenirse uyar.
- Sonuçlar sinyaldir, tavsiye değil. Kullanıcıyı kendi araştırmasına yönlendir.
- `confluence` veya `ensemble` stratejisi daha seçici/sağlam sonuç verir; doğruluk istenirse öner.

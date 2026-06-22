---
name: regime-check
description: Piyasanın genel rejimini (RISK_ON / NEUTRAL / RISK_OFF) analizbot ile ölçer — BTC trendi (200-EMA, ADX) + alt-coin breadth. Kullanıcı "piyasa nasıl", "risk-on mu risk-off mu", "boğa mı ayı mı", "şu an pozisyon açılır mı", "genel hava" gibi makro durum sorduğunda kullan.
---

# regime-check

Tek tek sinyallere bakmadan ÖNCE piyasanın genel havasını ölçer. Bu, en yüksek
etkili doğruluk kaldıracıdır: ayı rejiminde long, boğa rejiminde short açmaktan kaçınmak.

## Nasıl çalıştırılır
```
.venv\Scripts\python.exe -m src.app.cli regime --breadth-top 30
```
- `--breadth-top`: breadth için kaç likit sembol taranır. `--no-breadth`: sadece BTC trendi.

## Çıktıyı nasıl yorumla
- **Durum**: RISK_ON (boğa, long-yanlı), NEUTRAL (belirsiz/yatay), RISK_OFF (ayı, temkin).
- **Skor** (-1..+1): yönün gücü. **Breadth %**: sembollerin kaçı MA üstünde (>%60 sağlıklı, <%40 zayıf).
- **Maruziyet tavanı**: önerilen azami pozisyon yoğunluğu (rapor amaçlı).
- Kullanıcıya net bir cümleyle özetle: "Piyasa şu an RISK_OFF — yeni alımlarda temkinli ol."

## İlkeler
- Bu bir filtredir, kehanet değil. Rejim hızlı değişebilir; tarih/saat bağlamını belirt.
- RISK_OFF'ta "alım yapma" deme; "karşı-rejim işlemlerin tarihsel olarak daha düşük isabetli olduğunu" söyle.
- Analiz/tarama yaparken `--regime` bayrağıyla bu filtreyi otomatik uygulamayı öner.

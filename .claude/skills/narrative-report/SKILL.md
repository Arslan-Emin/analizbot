---
name: narrative-report
description: analizbot'un sayısal çıktısını (rejim, tarama, sinyaller, türev verisi) + haber/senaryo bağlamını insan-okur bir günlük/haftalık piyasa raporuna dönüştürür. Kullanıcı "rapor hazırla", "günlük özet", "haftalık bülten", "her şeyi topla" dediğinde kullan.
---

# narrative-report

Botun rakamlarını ve niteliksel analizi tek, akıcı bir markdown rapora dönüştürür.
İlham: anthropics/financial-services — equity-research/sector-overview rapor desenleri.

## Yöntem
1. **Veri topla** (botu çalıştır veya diğer skill çıktılarını kullan):
   - `regime-check` → genel hava
   - `market-screen` → öne çıkan AL/SAT adayları
   - belirli semboller için `crypto-analyze`
   - opsiyonel: `crypto-news-impact`, `scenario-analyzer`
2. **Sentezle** — şu yapıda markdown üret:
   - **Özet** (2-3 cümle): rejim + ana mesaj.
   - **Piyasa Rejimi**: durum, skor, breadth, ne anlama geldiği.
   - **Fırsatlar**: en güçlü 3-5 sinyal (sembol, yön, güven, kısa gerekçe, seviyeler).
   - **Haberler/Katalizörler** (varsa): etkili 3-5 başlık + kaynak.
   - **Riskler & İzlenecekler**: rejim/haber tetikleyicileri.
   - **Uyarı**: "Yatırım tavsiyesi değildir."
3. **Kaydet**: istenirse `./out/rapor_YYYYMMDD.md` olarak yaz (klasörü oluştur).

## İlkeler
- Her sayı bottan/kaynaktan gelmeli; uydurma. Çelişki varsa belirt.
- Tarih ve veri zamanını yaz. Kısa, taranabilir, madde-odaklı tut.
- Ton: nesnel ve ölçülü; aşırı iyimser/kötümser dil yok.

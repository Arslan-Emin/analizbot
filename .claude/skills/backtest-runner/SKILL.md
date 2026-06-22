---
name: backtest-runner
description: analizbot ile bir stratejiyi geçmiş veride test eder (backtest) veya walk-forward optimizasyon + sağlamlık/overfit analizi (optimize) yapar. Kullanıcı "şu strateji geçmişte nasıl performans gösterirdi", "backtest yap", "parametreleri optimize et", "rejim filtresi işe yarıyor mu", "overfit var mı" dediğinde kullan.
---

# backtest-runner

Stratejileri geçmiş veride look-ahead bias OLMADAN simüle eder ve dürüst metrikler üretir.

## İki mod

### 1) Backtest (tek parametre seti)
```
.venv\Scripts\python.exe -m src.app.cli backtest BTC/USDT --from 2025-01-01 --to 2026-01-01 -t 4h -S confluence [--regime]
```
Metrikler: işlem sayısı, kazanma oranı, ortalama R, maks. drawdown, Sharpe, toplam getiri.

**Doğruluk kanıtı:** Aynı komutu `--regime` ile ve `--no-regime` ile çalıştırıp
karşılaştır. Rejim filtresi tipik olarak drawdown'u düşürür, ortalama R'yi artırır.

### 2) Optimize (walk-forward + sağlamlık)
```
.venv\Scripts\python.exe -m src.app.cli optimize BTC/USDT --from 2024-01-01 --to 2026-01-01 -t 4h --train 750 --test 250
```
- Parametreyi IN-SAMPLE seçer, OUT-OF-SAMPLE'da test eder (config.walkforward.grid ızgarası).
- **OOS ortalaması** "gerçekte ne beklenir"in dürüst tahminidir — in-sample "en iyi"ye DEĞİL buna güven.
- **Sağlamlık**: "izole tepe" = overfit riski; "geniş plato" = parametreye duyarsız (iyi).

## İlkeler
- OOS negatifse dürüstçe söyle: "bu strateji bu dönemde edge göstermiyor". Sonucu süsleme.
- Backtest geçmiştir; gelecek garanti değildir. Komisyon/slippage dahil edildiğini belirt.
- Yeterli veri yoksa aralığı genişletmeyi veya timeframe büyütmeyi öner.

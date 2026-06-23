# analizbot — Kripto Analiz & Sinyal Botu

Binance kripto paralarını **ve ABD hisse senetlerini** analiz edip **BUY / SELL / HOLD**
sinyali, teknik analiz raporu ve **piyasa rejimi** değerlendirmesi üreten bir Python botu.

> **Varsayılan olarak READ-ONLY'dir:** sadece okur, hesaplar ve gerekçeli öneri sunar —
> bir **karar-destek aracıdır**, kâhin değildir. **Opsiyonel olarak**, açıkça
> etkinleştirilirse Binance Spot'ta **otonom işlem** yapabilir (varsayılan kademe
> **paper/simülasyon**; canlı gerçek-para için **üçlü güvenlik kilidi** şarttır —
> bkz. aşağıdaki **"Otonom İşlem"** bölümü).

## Özellikler

- **`analyze`** — Tek sembol için anında, gerekçeli analiz raporu (BUY/SELL/HOLD + güven %).
- **`watch`** — İzleme listesini periyodik tarar; sinyal **değiştiğinde** bildirir (spam yok).
- **`backtest`** — Stratejiyi geçmiş veride simüle eder; özet metrik üretir (**look-ahead bias yok**).
- **`train`** — ML modeli eğitir: RandomForest / HistGradientBoosting / **LightGBM / XGBoost**,
  walk-forward (TimeSeriesSplit) CV, opsiyonel hiperparametre araması ve olasılık kalibrasyonu,
  özellik önemi raporu.
- **`evaluate` + `performance`** — **Geri besleme / öğrenme döngüsü:** geçmiş sinyallerin
  sonucunu (isabet / R-multiple / Brier) ölçer; `--calibrate` ile güveni geçmiş isabete göre uyarlar.
- Zengin teknik analiz: EMA/RSI/MACD/ATR/ADX/Bollinger/OBV + **Stochastic, StochRSI, MFI, CCI,
  Williams %R, Supertrend, VWAP, Keltner ve mum formasyonları.**
- Bildirim: konsol (rich, renkli tablo) + opsiyonel Telegram.
- Kalıcılık: SQLite (sinyal, sonuç ve tarama geçmişi).
- **Piyasadan bağımsız çekirdek:** Kripto (Binance) **ve ABD hisse** (yfinance) aynı çekirdekle çalışır.

### Yeni özellikler (iki referans repodan esinlenildi)

İlham: [anthropics/financial-services](https://github.com/anthropics/financial-services) +
[tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills).

- **`regime`** — Piyasa rejimi (RISK_ON / NEUTRAL / RISK_OFF): BTC 200-EMA trendi + ADX + alt-coin
  **breadth**. `analyze`/`screen`/`backtest` komutlarına **`--regime`** ile sinyal kapılama
  (karşı-rejim sinyali zayıflatılır/elenir) — doğruluğu en çok artıran kaldıraç.
- **Funding rate + open interest** — `analyze` raporunda perpetual pozisyonlanma/contrarian sinyali;
  opsiyonel ML özelliği.
- **`ensemble` stratejisi** — ema_rsi + confluence + ml ağırlıklı oyla birleşir (opsiyonel dinamik ağırlık).
- **`optimize`** — Walk-forward optimizasyon + parametre sağlamlık/**overfit** analizi (OOS metrikleri).
- **Gelişmiş pozisyon boyutlama** — fixed_fractional / atr_target_vol / **Kelly** + portföy kısıtı.
- **`pairs`** — Cointegration tabanlı **pair trading** (istatistiksel arbitraj).
- **ABD hisse** — `analyze AAPL` gibi semboller otomatik `yfinance`'e yönlenir.
- **`thesis` + `coach`** — Tez yaşam döngüsü takibi (IDEA→ACTIVE→CLOSED, **MAE/MFE**) + 5-eksen performans koçu.
- **Otonom işlem (Binance Spot)** — `watch --execute` ile sinyale göre **paper/testnet/live** emir verir:
  **RiskManager** (stop-loss %5, günlük zarar kill-switch, pozisyon/maruziyet/cooldown limitleri),
  borsada koruyucu **STOP_LOSS_LIMIT**, onaylı/otonom mod ve `trade` paneli. Canlı için **üçlü kilit**.
- **Claude Skills** — `.claude/skills/` altında **9 skill** ile botu sohbetle kullanma.

## Seçilen varsayılanlar (neden?)

| Konu | Seçim | Gerekçe |
|---|---|---|
| Python | **3.12** | Olgun; tüm ekosistem (ML, vectorbt, vb.) sorunsuz. |
| İndikatörler | **El yazımı** (saf pandas/numpy) | `pandas-ta` orijinali ölü; tüm indikatörler (EMA/RSI/MACD/ATR/ADX/Bollinger/OBV + Stochastic/StochRSI/MFI/CCI/Williams %R/Supertrend/VWAP/Keltner/mum formasyonları) deterministik, öğretici, sıfır kırılgan bağımlılık. |
| ML modelleri | **sklearn + LightGBM/XGBoost** | rf/hgb sklearn ile bağımlılıksız; lgbm/xgb opsiyonel `ml-boost` extra'sı (`pip install -e .[ml-boost]`). |
| RSI/ATR yumuşatma | **Wilder (RMA)** | TradingView/standart konvansiyon. |
| Backtest | **Kendi motoru** (bar-bar) | Hiçbir kütüphane 3.14'i hedeflemiyordu; canlı/backtest paritesi + look-ahead denetlenebilirliği. |
| CLI | **Typer** | Tip-ipucu odaklı, alt-komut yapısı, rich ile uyumlu. |

## Kurulum (Windows)

Python 3.12 gereklidir.

```powershell
# 1) Sanal ortam (proje 3.12 ile)
py -3.12 -m venv .venv
.\.venv\Scripts\activate

# 2) Bağımlılıklar (çekirdek + Telegram + ML + statsmodels/yfinance dahil)
pip install -r requirements.txt
# (Alternatif, editable kurulumda opsiyonel ekstralar:)
#   pip install -e ".[ml-boost,pairs,equities]"   # lgbm/xgb + pair trading + ABD hisse

# 3) (Opsiyonel) gizli ayarlar
copy .env.example .env   # sonra .env'i düzenle
```

## Konfigürasyon

İki ayrı yer vardır:

- **`.env`** — *gizli* ayarlar (API anahtarları, Telegram token). Git'e **girmez**. Şablon: `.env.example`.
- **`config.yaml`** — *gizli olmayan* ayarlar: strateji parametreleri, izleme listesi, tarama aralığı.

`.env` (hepsi opsiyonel; otonom işlem yapmıyorsan anahtar gerekmez):
```
BINANCE_API_KEY=             # veri için GEREKMEZ; CANLI işlemde "Spot Trading" açık olmalı
BINANCE_API_SECRET=
BINANCE_TESTNET_API_KEY=     # execution.mode: testnet için (sahte para)
BINANCE_TESTNET_API_SECRET=
LIVE_TRADING=0               # CANLI işlem ana kilidi (üçlünün 1.'si); 0 = kapalı
TELEGRAM_BOT_TOKEN=          # Telegram bildirimi için
TELEGRAM_CHAT_ID=
LOG_LEVEL=INFO
DB_URL=sqlite:///signals.db
```

`config.yaml` bölümleri: `strategies` (ema_rsi / confluence / ml / **ensemble**), **`regime`**
(rejim filtresi), **`data`** (funding/OI), **`sizing`** (pozisyon boyutlama), **`walkforward`**
(optimize ızgarası), **`pairs`** (cointegration), `learning` (geri besleme), `notify`,
**`execution`** (otonom işlem — varsayılan kapalı/paper).

## Kullanım

```powershell
# Tek seferlik analiz (API anahtarı gerekmez)
python -m src.app.cli analyze BTC/USDT
python -m src.app.cli analyze ETH/USDT --timeframe 4h --limit 300

# İzleme modu (watchlist'i periyodik tara; --once ile tek tarama)
python -m src.app.cli watch --once
python -m src.app.cli watch

# Backtest (geçmiş veride simülasyon)
python -m src.app.cli backtest BTC/USDT --from 2024-01-01 --to 2024-06-30 --timeframe 4h

# Pariteleri listele (Binance spot)
python -m src.app.cli symbols --quote USDT --search PEPE

# Tüm piyasayı tara, en güçlü fırsatları sırala
python -m src.app.cli screen --quote USDT -m 50 --strategy confluence

# ML modeli eğit (model seçimi + ayar + kalibrasyon), sonra ML stratejisiyle analiz et
python -m src.app.cli train BTC/USDT --from 2024-01-01 --to 2024-12-31 -t 4h --model lgbm --tune --calibrate
python -m src.app.cli analyze BTC/USDT --strategy ml -t 4h

# Öğrenme döngüsü: sinyal sonuçlarını çöz → performansı gör → güveni kalibre et
python -m src.app.cli evaluate                       # açık sinyalleri geçmiş veriyle çöz
python -m src.app.cli performance                    # isabet / R / Brier (strateji-sembol kırılımı)
python -m src.app.cli analyze BTC/USDT --calibrate   # güveni geçmiş isabete göre ayarla
```

### Yeni komutlar

```powershell
# Piyasa rejimi (RISK_ON / NEUTRAL / RISK_OFF) — BTC trendi + breadth
python -m src.app.cli regime

# Rejim filtresiyle analiz / tarama / backtest (karşı-rejim sinyalini zayıflat veya ele)
python -m src.app.cli analyze BTC/USDT -t 4h --regime
python -m src.app.cli screen -q USDT -m 50 --regime
python -m src.app.cli backtest BTC/USDT --from 2025-01-01 --to 2026-01-01 -t 4h --regime

# Ensemble stratejisi (ema_rsi + confluence + ml birleşik ağırlıklı oy)
python -m src.app.cli analyze BTC/USDT -t 4h --strategy ensemble

# Walk-forward optimizasyon + overfit / sağlamlık analizi (OOS metrikleri)
python -m src.app.cli optimize BTC/USDT --from 2024-01-01 --to 2026-01-01 -t 4h --train 750 --test 250

# Pair trading (cointegration / istatistiksel arbitraj)
python -m src.app.cli pairs BTC/USDT ETH/USDT -t 4h

# ABD hisse senedi (otomatik yfinance) — kripto ile aynı komutlar
python -m src.app.cli analyze AAPL -t 1d

# Performans koçu (5-eksen disiplin değerlendirmesi)
python -m src.app.cli coach

# Tez (fikir) yaşam döngüsü takibi: IDEA → ENTRY_READY → ACTIVE → CLOSED
python -m src.app.cli thesis create BTC/USDT --dir long --text "200-EMA üstü kırılım" --entry 60000 --stop 58000 --tp 65000
python -m src.app.cli thesis list
python -m src.app.cli thesis advance 1 --to ENTRY_READY
python -m src.app.cli thesis advance 1 --to ACTIVE
python -m src.app.cli thesis close 1            # gerçekleşen getiri + MAE/MFE postmortem
```

Strateji seçimi: `--strategy ema_rsi|confluence|ml|ensemble` (veya `config.yaml > active_strategy`).

Her `analyze` çıktısı: sembol/zaman/son fiyat, sinyal + güven %, **madde madde gerekçeler**,
indikatör tablosu (RSI, EMA, MACD, ATR, 24-bar değişim/hacim), öneri seviyeleri (giriş/stop/hedef + R:R)
ve "yatırım tavsiyesi değildir" uyarısı.

## Stratejiler

Üç strateji vardır (hepsi `Strategy` arayüzünü uygular, takas edilebilir):

- **`ema_rsi`** (varsayılan) — basit, hızlı; EMA trend + RSI + MACD.
- **`confluence`** — gelişmiş; ema_rsi + ADX (trend gücü) + Bollinger + hacim/OBV
  + **çok zaman dilimli (MTF) onay** (1h sinyalini 4h trendiyle teyit eder).
- **`ml`** — makine öğrenmesi. Önce `train` ile model eğitilir; model `horizon` bar ilerideki
  yönü (±eşik %) tahmin eder. Model tipi seçilebilir: `rf` (RandomForest), `hgb`
  (HistGradientBoosting), `lgbm` (LightGBM), `xgb` (XGBoost). Özellikler genişletilmiştir
  (Stochastic, StochRSI, MFI, CCI, Williams %R, Supertrend, VWAP, Keltner, mum formasyonları,
  döngüsel saat/gün). *Not: kripto gürültülüdür, ML'in temel çizgiyi geçmesi garanti değildir —
  sonuçlara temkinli yaklaşın.*
  - `confluence` ayrıca config bayraklarıyla genişletilebilir: `use_supertrend`, `use_mfi`
    (varsayılan kapalı; açılırsa ek onay koşulu olarak eklenir).
- **`ensemble`** — Yukarıdaki stratejileri **ağırlıklı oyla** birleştirir; en az `min_agreement`
  üye aynı yöndeyse BUY/SELL üretir, aksi HOLD. `config.yaml > strategies.ensemble` altında üye
  ağırlıkları ayarlanır; `dynamic_weight: true` ile ağırlıklar geçmiş isabete (hit_rate) göre uyarlanır.

### EmaRsiStrategy (varsayılan)

EMA trend + RSI aşırılık + MACD onayını birleştiren kural-tabanlı, **deterministik** bir stratejidir.

- **BUY:** Skor ≥ 2 **ve** RSI aşırı alımda değil **ve** (yukarı trend veya yukarı kesişim).
- **SELL:** Skor ≤ −2 **ve** (aşağı trend / aşağı kesişim / RSI aşırı alım).
- **HOLD:** Sinyaller zayıf/dengeli ("net değil").
- **Güven** = sağlanan onay sayısı / toplam olası onay.
- **Seviyeler** ATR'ye dayalıdır (stop = ATR×1.5, hedef = ATR×3 → R:R ~1:2). Bunlar
  **örnek/eğitseldir, emir değildir.**

Parametreler `config.yaml > strategies.ema_rsi` altından ayarlanır.

## Öğrenme / geri besleme döngüsü

Bot ürettiği sinyalleri `signals.db`'ye yazar; **`evaluate`** komutu bunların sonucunu
geçmiş veriyle çözer (stop mu hedef mi doldu, gerçekleşen getiri, R-multiple) — backtest
ile **aynı çıkış semantiği** (`src/core/simulate.py`, tek kaynak). **`performance`** komutu
strateji/sembol kırılımında isabet oranı, ortalama R ve **Brier skoru** (güvenin gerçekle
kalibrasyonu; düşük = iyi) verir.

`analyze`/`screen`/`watch` komutlarına **`--calibrate`** eklenirse, güven skoru geçmiş
isabete göre ayarlanır: her güven kovasının ampirik kazanma olasılığı **Bayesyen küçültme**
ile hesaplanır (az örnekli kova genel ortalamaya çekilir → az veriyle aşırı tepki yok).
Yeterli çözülmüş sinyal birikene dek (`learning.min_samples_for_calibration`) ham skor kullanılır.

```powershell
python -m src.app.cli evaluate                 # açık sinyalleri çöz (periyodik çalıştır)
python -m src.app.cli performance              # isabet / R / Brier tablosu
python -m src.app.cli analyze BTC/USDT --calibrate
```

Ayarlar: `config.yaml > learning` (`eval_horizon_bars`, `min_samples_for_calibration`,
`calibration_bins`).

## Piyasa rejimi filtresi (doğruluk için en etkili kaldıraç)

`regime` komutu piyasayı **RISK_ON / NEUTRAL / RISK_OFF** olarak sınıflar: benchmark (BTC) 200-EMA
trendi + ADX + alt-coin **breadth** (sembollerin % kaçı MA üstünde). `analyze`/`screen`/`backtest`
komutlarına **`--regime`** eklenince karşı-rejim sinyalleri kapılanır:

- **soft** (varsayılan): karşı-rejim sinyalin **güveni düşürülür** (sıralama/kalibrasyon için).
- **gate**: karşı-rejim sinyali **HOLD'a düşürülür** (backtest'te işlemi eler).

Backtest motoru işlemleri *action*'a göre açtığından, "filtreli vs filtresiz" karşılaştırmasında
`backtest --regime` **gate** modunu zorlar (soft mod girişleri etkilemez). Ayarlar:
`config.yaml > regime`. Varsayılan **kapalı** (`enable: false`); açmak için `regime.enable: true`
ya da komutta `--regime`. Look-ahead yoktur: backtest'te her bar için yalnız o ana dek kapanmış
günlük rejim kullanılır.

## Türev verisi: funding rate + open interest

`config.yaml > data` ile `analyze` raporu **funding rate** (contrarian duygu: aşırı pozitif =
kalabalık long = aşırı ısınma) ve **open interest** (eğilim) gösterir. Spot sembol otomatik
perpetual'a çevrilir (`BTC/USDT` → `BTC/USDT:USDT`). `use_funding_features: true` ise `train`
komutu funding'i (look-ahead'siz hizalanmış) bir ML özelliği olarak ekler. *(Binance OI geçmişi
~30 günle sınırlı olduğundan OI canlı bir göstergedir, ML özelliği değildir.)*

## Pozisyon boyutlama

`config.yaml > sizing.method` (tüm stratejilere uygulanır; strateji bloğu override edebilir):

- **fixed_fractional** (varsayılan): işlem başına sabit % risk (stop mesafesine göre boyut).
- **atr_target_vol**: hedef oynaklığa göre boyut (düşük volatilitede büyür, yüksekte küçülür).
- **kelly**: geçmiş isabet/ödülden **yarım-Kelly** (yalnız `analyze`'da canlı istatistikten;
  backtest'te look-ahead olmasın diye fixed_fractional'a düşer).

`max_position_pct` tek pozisyonu sermayenin belirli %'iyle sınırlar. Boyutlar **örnek/eğitseldir**.

## Pair trading (cointegration)

`pairs A B` iki sembolde **cointegration** testi (statsmodels), hedge oranı ve spread **z-skoru**
hesaplar; spread aşırı açıldığında ucuzu AL / pahalıyı SAT (mean reversion). Yarı-ömür ile dönüş
hızı raporlanır. Ayarlar: `config.yaml > pairs` (z_entry, z_exit, coint_pvalue). Gerekli ekstra:
`pip install -e ".[pairs]"` (requirements.txt zaten içerir).

## Tez takibi + performans koçu

- **`thesis`** — bir fikri yaşam döngüsünde izle: **IDEA → ENTRY_READY → ACTIVE → CLOSED**
  (+ INVALIDATED). Geçiş kuralları doğrulanır. Kapanışta gerçekleşen getiri + **MAE/MFE**
  (en kötü/en iyi sapma) postmortemi hesaplanır. Alt komutlar: `create / list / show / advance /
  close / invalidate`.
- **`coach`** — çözülmüş sinyalleri 5 eksende değerlendirir (beklenti, risk disiplini, tutarlılık,
  kalibrasyon, örneklem) ve OK / WARN / REVIEW verdikti + öneri verir.

## Claude Skills (sohbetle kullanım)

`.claude/skills/` altında **9 skill** vardır (ayrıntı: `.claude/skills/README.md`). Claude Code/Cowork
içinde proje kökünde sohbet ederken doğal dille tetiklenir:

- "BTC'yi analiz et" → `crypto-analyze` · "piyasa risk-on mu?" → `regime-check`
- "hangi coinlerde fırsat var?" → `market-screen` · "backtest yap / overfit var mı?" → `backtest-runner`
- "son haberler ne etkiliyor?" → `crypto-news-impact` · "ne olabilir?" → `scenario-analyzer`
- "hangi sektör/anlatı sıcak?" → `theme-detector` · "rapor hazırla" → `narrative-report`
- "günlük rutini çalıştır" → `daily-workflow` (rejim → tarama → analiz → haber → rapor zinciri)

## Otonom İşlem (Binance Spot) — güvenlik öncelikli

Bot, açıkça etkinleştirilirse ürettiği **ensemble + rejim** sinyaline göre Binance Spot'ta
(long-only) otomatik **alım/satım** yapabilir. **Varsayılan davranış değişmez:** `execution.enabled:
false` ve `--execute` yokken bot tamamen read-only kalır.

**Üç kademe (`config.yaml > execution.mode`):**
- **`paper`** (VARSAYILAN) — simülasyon; API yok, gerçek para yok. Canlı fiyatla dolum simüle edilir.
- **`testnet`** — Binance testnet (sahte para). `.env`'de `BINANCE_TESTNET_API_KEY/SECRET` gerekir.
- **`live`** — GERÇEK PARA. Yalnız **üçlü kilit** ile.

**Canlı için ÜÇLÜ KİLİT (üçü birden):**
1. `.env` → `LIVE_TRADING=1`
2. `config.yaml` → `execution.mode: live`
3. CLI → `--live`

Biri eksikse canlı emir **reddedilir** (bot read-only sürer). Ayrıca emir vermek için
`execution.enabled: true` **ve** `watch --execute` şarttır.

**Güvenlik ağları:**
- **RiskManager** her girişten önce: stop-loss **%5**, **kill-switch** (günlük zarar
  `max_daily_loss_pct`'i aşarsa yeni giriş yok), `max_concurrent_positions`,
  `max_position_pct`, `max_total_exposure_pct`, `cooldown_minutes`, `min_order_usdt`,
  `allocation_quote_cap` (botun kullanacağı azami USDT).
- Girişten sonra borsaya **koruyucu STOP_LOSS_LIMIT** satış konur → bot kapalıyken bile %5 stop korur.
- Her taramada **mutabakat**: borsadaki bakiye/emirle yerel durum eşitlenir; pozisyon
  varsa tekrar açılmaz (piramitleme yok).
- **Karar modu:** `confirm` (varsayılan; emir önce onay bekler) veya `auto` (otonom).

```powershell
# Paper (simülasyon) otonom tarama — önce config'te execution.enabled: true yapın
python -m src.app.cli watch --execute --once

# trade paneli (kademe config.execution.mode'a göre)
python -m src.app.cli trade status            # kademe, açık pozisyon, maruziyet, günlük PnL
python -m src.app.cli trade positions         # açık pozisyonlar (--all: kapanmışlar da)
python -m src.app.cli trade pending            # onay bekleyen niyetler (confirm modu)
python -m src.app.cli trade approve 3          # bir niyeti onayla → emir
python -m src.app.cli trade reject 3           # bir niyeti reddet
python -m src.app.cli trade close BTC/USDT     # bir pozisyonu market kapat
python -m src.app.cli trade panic              # ACİL: tüm pozisyonları kapat + niyetleri reddet

# CANLI (yalnız üçlü kilitle; küçük allocation_quote_cap ile başlayın)
#   .env: LIVE_TRADING=1   ·   config: execution.mode: live, enabled: true
python -m src.app.cli watch --execute --live
```

**Önerilen yol:** paper → testnet → küçük canlı. Backtest geçmişi canlıda kayma/gecikme/kısmi
dolumla farklılaşır; kill-switch ve %5 stop zararı sınırlar ama **sıfırlamaz**. Sorumluluk kullanıcıdadır.

## Binance API kurulumu (opsiyonel)

1. Genel piyasa verisi (OHLCV/fiyat) için **anahtar gerekmez** — bot anahtarsız çalışır.
2. Yalnız veri/analiz için anahtar: **"Enable Reading" AÇIK; "Spot/Futures Trading" ve "Withdrawals" KAPALI.**
3. **CANLI otonom işlem** için: **"Enable Spot Trading" AÇIK; "Withdrawals" KAPALI** + **IP whitelist** zorunlu.
4. Testnet için ayrı anahtar: https://testnet.binance.vision → `BINANCE_TESTNET_API_KEY/SECRET`.
5. Anahtarları yalnızca `.env`'e koyun; repoya asla commit etmeyin (loglanmaz).

## Mimari (adaptör deseni)

```
MarketDataProvider (ABC) ──> CcxtBinanceData (kripto)  |  YFinanceData (ABD hisse)
        │  ortak DataFrame                         (+ funding/OI: src/core/derivatives)
        ▼
AnalysisEngine ─> Strategy (ABC) ─> EmaRsi / Confluence / Ml / Ensemble
        │  Signal / AnalysisResult     (+ RegimeFilteredStrategy sarmalayıcı: rejim kapılama)
        │                              (opsiyonel ConfidenceCalibrator ile güven kalibrasyonu)
        ├──> Notifier (konsol / Telegram)
        └──> Storage (SQLite: sinyaller + sonuçlar + tezler + emirler/pozisyonlar)
                  ▲
   Öğrenme: src/learning (evaluator → stats → calibrator/coach)  ←  src/core/simulate
   Rejim:   src/core/regime (trend + breadth)   ·   Pair: src/strategies/pairs (cointegration)
   Otonom:  src/execution (OrderExecutor ABC: Paper/BinanceSpot + RiskManager + ExecutionManager)
            watch --execute / trade  →  factory üçlü kilit  →  emir + koruyucu stop + DB (exec_*)
Tetikleyici: CLI komutu  veya  watch scheduler   ·   Claude Skills: .claude/skills/
```

`AnalysisEngine` ve `Strategy` yalnızca **arayüzlere** bağımlıdır; somut Binance sınıfını
import etmez. Çekirdek/strateji **execution modülünü import etmez** — yalnız watch döngüsü
(`scheduler`) kullanır; `OrderExecutor` ABC sayesinde paper/testnet/live aynı arayüzle eklenir.

## ABD hisse senedi desteği (eklendi)

`src/data/yfinance_data.py` (`YFinanceData`) ile ABD hisseleri/endeksleri desteklenir.
`market_registry.get_provider()` sembol desenine göre **otomatik** yönlendirir: `BTC/USDT` → Binance;
`AAPL` / `SPY` / `^VIX` → yfinance. Tüm stratejiler ve komutlar (analyze/backtest/optimize…) değişmeden
hissede çalışır; `is_market_open()` kaba NYSE saatini uygular.

```powershell
pip install -e ".[equities]"          # (requirements.txt zaten içerir)
python -m src.app.cli analyze AAPL -t 1d
python -m src.app.cli backtest MSFT --from 2023-01-01 --to 2026-01-01 -t 1d
```

- yfinance interval kısıtı: `1d` / `1h` / `1wk`… desteklenir, **`4h` yoktur**.
- Hisse için piyasa rejimi: `config.yaml > regime.benchmark: SPY` yapın (varsayılan BTC/USDT).
- Varsayılan hisse evreni (screen/breadth): `YFinanceData.DEFAULT_UNIVERSE` (~30 büyük-sermaye).

## Testler

```powershell
python -m pytest -q          # 135 birim + uçtan uca test (ağsız, deterministik)
python -m ruff check src tests
```

Test verisi sentetiktir (`tests/fixtures/ohlcv_btcusdt.csv` + seed'li sentetik seriler);
ağ bağımlılığı yoktur. Yeni modüllerin (rejim, türev, ensemble, walk-forward, sizing, pairs,
yfinance, tez, koç) her biri için birim testi vardır.

## Yasal not / sorumluluk reddi

Bu yazılım **eğitim ve kişisel kullanım** amaçlıdır, **yatırım tavsiyesi değildir**.
Üretilen sinyaller geçmiş veriye ve sabit kurallara dayanır; gelecekteki fiyatları
garanti etmez. Kripto işlemleri yüksek risklidir ve sermaye kaybına yol açabilir.
Kullanıcı kendi kararlarından ve risk yönetiminden sorumludur. Türkiye'de kripto
varlık hizmet sağlayıcılar SPK düzenlemesine tabidir; lisanslı bir platform kullanın.

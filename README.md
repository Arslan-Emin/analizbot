# analizbot — Kripto Analiz & Sinyal Botu

Binance üzerindeki kripto paraları analiz edip **BUY / SELL / HOLD** sinyali ve
teknik analiz raporu üreten bir Python botu.

> **Bu bot GERÇEK EMİR GÖNDERMEZ, bakiyeye/paraya dokunmaz.** Sadece okur,
> hesaplar ve gerekçeli öneri sunar. Bir **karar-destek aracıdır**, kâhin değildir.

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
- **Piyasadan bağımsız çekirdek:** İleride ABD borsası yalnız yeni bir veri adaptörüyle eklenebilir.

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

# 2) Bağımlılıklar
pip install -r requirements.txt

# 3) (Opsiyonel) gizli ayarlar
copy .env.example .env   # sonra .env'i düzenle
```

## Konfigürasyon

İki ayrı yer vardır:

- **`.env`** — *gizli* ayarlar (API anahtarları, Telegram token). Git'e **girmez**. Şablon: `.env.example`.
- **`config.yaml`** — *gizli olmayan* ayarlar: strateji parametreleri, izleme listesi, tarama aralığı.

`.env` (hepsi opsiyonel):
```
BINANCE_API_KEY=        # genel veri için GEREKMEZ; verilirse SADECE read-only
BINANCE_API_SECRET=
TELEGRAM_BOT_TOKEN=     # Telegram bildirimi için
TELEGRAM_CHAT_ID=
LOG_LEVEL=INFO
DB_URL=sqlite:///signals.db
```

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

Strateji seçimi: `--strategy ema_rsi|confluence|ml` (veya `config.yaml > active_strategy`).

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

## Binance API kurulumu (opsiyonel)

1. Genel piyasa verisi (OHLCV/fiyat) için **anahtar gerekmez** — bot anahtarsız çalışır.
2. Anahtar kullanılacaksa: Binance > API Management > yeni anahtar.
   **"Enable Reading" AÇIK; "Spot/Futures Trading" ve "Withdrawals" KAPALI.** IP kısıtlaması ekleyin.
3. Anahtarları yalnızca `.env`'e koyun; repoya asla commit etmeyin.

## Mimari (adaptör deseni)

```
MarketDataProvider (ABC) ──> CcxtBinanceData        (ileride: YFinanceData / IBKRData)
        │  ortak DataFrame
        ▼
AnalysisEngine  ──>  Strategy (ABC) ──> EmaRsiStrategy / Confluence / Ml
        │  Signal / AnalysisResult   (opsiyonel ConfidenceCalibrator ile güven kalibrasyonu)
        ├──> Notifier (konsol / Telegram)
        └──> Storage (SQLite: sinyaller + sonuçlar)
                  ▲
        Öğrenme:  src/learning (evaluator → stats → calibrator)  ←  src/core/simulate (çıkış simülasyonu)
Tetikleyici: CLI komutu  veya  watch scheduler
```

`AnalysisEngine` ve `Strategy` yalnızca **arayüzlere** bağımlıdır; somut Binance sınıfını
import etmez. Çekirdeğe `if market == "crypto"` mantığı sızmaz.

## ABD borsasını sonradan ekleme

Çekirdeği değiştirmeden:
1. `src/data/us_equity.py` → `MarketDataProvider`'ı uygula (`yfinance`/IBKR), `market="us_equity"`.
2. `is_market_open()` içinde NYSE/Nasdaq takvimini uygula (kripto her zaman `True`).
3. `market_registry.get_provider()` sembol desenine göre doğru sağlayıcıya yönlendirsin.
4. Strateji aynı kalır (piyasadan bağımsız).

## Testler

```powershell
python -m pytest -q          # birim + uçtan uca testler (ağsız, deterministik)
python -m ruff check src tests
```

Test verisi sentetiktir (`tests/fixtures/ohlcv_btcusdt.csv`); ağ bağımlılığı yoktur.

## Yasal not / sorumluluk reddi

Bu yazılım **eğitim ve kişisel kullanım** amaçlıdır, **yatırım tavsiyesi değildir**.
Üretilen sinyaller geçmiş veriye ve sabit kurallara dayanır; gelecekteki fiyatları
garanti etmez. Kripto işlemleri yüksek risklidir ve sermaye kaybına yol açabilir.
Kullanıcı kendi kararlarından ve risk yönetiminden sorumludur. Türkiye'de kripto
varlık hizmet sağlayıcılar SPK düzenlemesine tabidir; lisanslı bir platform kullanın.

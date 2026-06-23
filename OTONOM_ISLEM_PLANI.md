# analizbot — Otonom İşlem (Binance Spot) — Uygulama Planı

> Bu, **sonra uygulanacak** otonom-işlem planıdır. Henüz koda dokunulmadı.
> Bot şu an hâlâ read-only'dir. Uygulama, bu belgedeki sıraya göre yapılacaktır.

## Bağlam (neden?)

Bot bugüne dek bilinçli olarak **read-only** idi: BUY/SELL/HOLD sinyali üretir, **asla gerçek
emir göndermez**. Amaç artık botu **kendi kendine işlem açan** hale getirmek, öncelikle
**Binance Spot**'ta. Bu, projenin temel ilkesini tersine çevirir ve **gerçek parayla** çalışır →
tasarım **güvenlik-öncelikli** olmalı.

**Kararlar:**
- **Piyasa:** Spot (long-only). BUY → pozisyon aç; SELL/HOLD → pozisyonu KAPAT. Kaldıraç/short yok.
- **Karar modu:** Hem **otonom** hem **onaylı** kurulacak, config'ten seçilir (başlangıçta onaylı).
- **Kademe:** Paper + Testnet + Canlı üçü de kurulur; **varsayılan paper**. Canlı yalnız **üçlü
  kilit** ile: `.env > LIVE_TRADING=1` **ve** `config > execution.mode: live` **ve** CLI `--live`.
- **Risk:** Korumacı profil + **stop-loss %5**. Günlük zarar kill-switch, pozisyon ve maruziyet limitleri.

**Hedef:** watch döngüsü, ensemble+rejim sinyaline göre Binance Spot'ta otomatik (veya onaylı)
alım/satım yapar; sıkı risk limitleri ve kill-switch ile; önce paper/testnet'te kanıtlanır.

## ⚠️ Güvenlik mimarisi (en önemli kısım)

1. **Üç kademe (`execution.mode`):** `paper` (simülasyon, API yok — VARSAYILAN) · `testnet`
   (Binance testnet, sahte para) · `live` (gerçek para).
2. **Canlı için üçlü kilit (hepsi gerekli):** `LIVE_TRADING=1` (.env) + `execution.mode: live`
   (config) + `--live` (CLI). Biri eksikse canlı emir REDDEDİLİR ve uyarı verilir. Ayrıca
   `execution.enabled: true` ve `watch --execute` şart.
3. **Kill-switch:** Günlük gerçekleşen zarar `max_daily_loss_pct`'i aşarsa yeni giriş yok (gün
   sonuna dek). Kalıcı günlük PnL takibi (DB).
4. **Limitler:** `max_concurrent_positions`, `max_position_pct`, `max_total_exposure_pct`,
   `risk_per_trade_pct`, `stop_loss_pct=5`, `min_order_usdt`, `cooldown_minutes`,
   `allocation_quote_cap` (botun kullanacağı azami USDT — canlıda küçük tutulması önerilir).
5. **İdempotensi & mutabakat:** Her taramada borsadaki bakiye/açık emirlerle yerel durum
   eşitlenir; zaten pozisyondaysa tekrar açmaz; client order id ile çift-emir önlenir.
6. **Koruyucu stop borsada durur:** Girişten sonra borsaya STOP_LOSS_LIMIT satış emri konur →
   bot kapalıyken bile %5 stop korur (offline güvenlik ağı).
7. **Anahtar izinleri:** SPOT TRADING açık, **WITHDRAWAL KAPALI**, IP whitelist. `.env`'de saklanır,
   asla loglanmaz.
8. **panic komutu:** Tüm açık emirleri iptal + tüm pozisyonları piyasadan kapat (acil durdurma).

## Yeni modül: `src/execution/`

| Dosya | Sorumluluk |
|---|---|
| `models.py` | `ExecMode`(paper/testnet/live), `DecisionMode`(confirm/auto), `OrderIntent`, `OrderResult`, `PositionState` |
| `base.py` | `OrderExecutor` ABC: `buy(symbol, quote_amount)`, `sell_all(symbol)`, `place_protective_stop(...)`, `cancel(...)`, `fetch_position(symbol)`, `free_quote()`, `reconcile()` |
| `paper.py` | `PaperExecutor` — simüle dolum (canlı fiyatı provider'dan alır), API yok. VARSAYILAN, testlerin temeli |
| `binance_spot.py` | `BinanceSpotExecutor` — ccxt; `set_sandbox_mode` ile testnet; `create_market_buy_order_with_cost`, `amount_to_precision`, `market()` min-notional; STOP_LOSS_LIMIT koruyucu emir |
| `risk.py` | `RiskManager` — giriş öncesi tüm limit kontrolleri + kill-switch + boyut hesabı (gerçek bakiye × risk%, %5 stop, caps, min-notional) |
| `manager.py` | `ExecutionManager` — `on_signal(result, prev_action, position)`: sinyali pozisyonla bağdaştır → karar → risk kontrol → (auto: emir / confirm: PENDING + bildir) → DB + notifier |

**Mimari ilke korunur:** Çekirdek/strateji bu modülü import etmez; yalnız watch döngüsü kullanır.
`OrderExecutor` ABC sayesinde paper/testnet/live ve ileride futures aynı arayüzle eklenir.

## Akış — Spot long-only (pozisyon-farkında)

`scan_once` her sembol için (ensemble+rejim sinyali üretildikten sonra):
- **Pozisyon YOK + BUY** → boyut = RiskManager(gerçek bakiye, risk%, %5 stop, caps); `min_order_usdt`
  ve `min_notional` geçerse market alım; ardından borsaya **%5 koruyucu STOP_LOSS_LIMIT** satış.
- **Pozisyon VAR + (SELL veya HOLD)** → market satışla kapat + koruyucu stop'u iptal et.
- **Pozisyon VAR + BUY** → bir şey yapma (zaten long; piramitleme yok).
- **Take-profit:** sinyalin `take_profit`'i (yoksa `take_profit_pct`) → fiyat ulaşınca kapat.
- **Mutabakat:** Tarama başında borsa pozisyonu/emirleri çekilir; koruyucu stop dolmuşsa pozisyon
  kapalı işaretlenir.

`confirm` modunda emir YERİNE `pending_intents`'e yazılır + bildirilir; kullanıcı `trade approve`/
`trade reject` ile karar verir (Telegram buton onayı sonraki aşama).

## Depolama (`src/storage/db.py`)

Mevcut idempotent migrasyon desenine yeni tablolar (create_all otomatik kurar):
- `exec_orders` — verilen her emir (symbol, side, type, qty, price, exchange_order_id, status,
  fill_price/qty, error, mode, created_at).
- `exec_positions` — açık/kapalı pozisyon (symbol, entry_price, qty, stop_price, tp_price, status,
  pnl_quote, opened/closed_at, protective_order_id).
- `pending_intents` — onaylı modda bekleyen emir niyetleri.
- `exec_daily_pnl` — günlük gerçekleşen PnL (kill-switch için).
Repository metotları mevcut desen: `save_*`, `list_*`, `update_*`.

## Değişecek mevcut dosyalar

- **`src/app/scheduler.py`** — `WatchScanner`'a opsiyonel `ExecutionManager`; `scan_once` ~line 81'de
  `notifier.send_signal` sonrası `exec_manager.on_signal(...)`. **Ek düzeltme:** watch döngüsüne
  **rejim filtresi** + dinamik-ensemble/kelly param eklenmesi (şu an yalnız `analyze`/`screen`
  uyguluyor — bkz. `cli._wrap_live_regime`, `_maybe_dynamic_ensemble`). Böylece otonom işlemler de
  rejim-filtreli olur.
- **`src/config.py`** — `Settings`'e `live_trading: bool` (LIVE_TRADING), `binance_testnet_api_key/secret`.
- **`src/app/cli.py`** — `watch` komutuna `--execute` ve `--live`; yeni `trade` sub-app:
  `status`, `positions`, `pending`/`approve`/`reject`, `close <SYMBOL>`, `panic`.
- **`config.yaml`** — yeni `execution:` bölümü (aşağıda).
- **`.env.example`** — `LIVE_TRADING=0`, testnet anahtarları, izin notu (SPOT açık/WITHDRAW kapalı).
- **README + DISCLAIMER + docstring'ler** — "asla emir göndermez" iddiaları güncellenecek; yeni
  otonom-işlem bölümü + güvenlik uyarıları (`src/notify/format.py` DISCLAIMER dahil).

## config.yaml — `execution:` (varsayılanlar güvenli)

```yaml
execution:
  enabled: false             # ana şalter (watch --execute de gerekir)
  mode: paper                # paper | testnet | live
  decision: confirm          # confirm (onaylı) | auto (otonom)
  market: spot               # şimdilik spot (futures sonra)
  timeframe: 4h              # işlem için önerilen (scalping'ten kaçın)
  risk_per_trade_pct: 1.0    # korumacı
  stop_loss_pct: 5.0         # KORUYUCU STOP %5
  take_profit_pct: 10.0      # sinyal TP yoksa kullanılır (R:R ~1:2)
  max_position_pct: 20       # tek pozisyon, sermaye %'i
  max_total_exposure_pct: 40 # tüm açık pozisyonlar toplamı
  max_concurrent_positions: 2
  max_daily_loss_pct: 3.0    # kill-switch
  min_order_usdt: 11         # Binance min notional ~10 + emniyet
  cooldown_minutes: 60       # aynı sembolde işlemler arası
  allocation_quote_cap: 0    # 0 = serbest USDT bakiyesi; >0 = bu tutarla sınırla
```

## Doğrulama (uçtan uca)

1. **Birim testleri (ağsız, mock'lu):** `tests/test_execution_paper.py` (giriş/çıkış/%5 stop/TP),
   `tests/test_risk.py` (limitler + kill-switch + boyut + min-notional), `tests/test_exec_manager.py`
   (BUY→aç, SELL/HOLD→kapat, BUY→no-op; confirm→pending), `tests/test_exec_storage.py`.
2. **Paper koşu:** `execution.enabled=true, mode=paper, decision=auto` →
   `watch --execute --once` → simüle pozisyonlar DB'ye; `trade status` gösterir.
3. **Testnet e2e (manuel):** `.env` testnet anahtarları, `mode=testnet` → `watch --execute --once`
   → testnet'te gerçek emir; panelden doğrula; `trade close`/`trade panic`.
4. **Onaylı mod:** `decision=confirm` → PENDING + bildirim; `trade approve <id>` / `trade reject <id>`.
5. **Canlı (yalnız kullanıcı, gözetimli):** küçük `allocation_quote_cap`, üçlü kilit, tek sembol,
   bir tarama; Binance'te dolum + koruyucu stop doğrulanır; `trade panic` test edilir.
6. Tüm mevcut testler geçmeli (`pytest -q`), ruff temiz.

## Riskler / sınırlar (dürüst)

- **Gerçek para riski:** Backtest geçmiştir; canlıda kayganlık/gecikme/kısmi dolum farklı olur.
  Önce paper→testnet→küçük canlı. Kill-switch ve %5 stop zararı sınırlar ama sıfırlamaz.
- **Spot OCO yok (ccxt):** Koruyucu stop + TP ayrı emirler; biri dolunca diğeri iptal (mutabakat).
- **Watch süreklilik:** Otonom mod için `watch`'ın sürekli çalışması gerekir; bot kapalıyken yalnız
  borsadaki koruyucu stop devrede.
- **Min notional / küçük bakiye:** `min_order_usdt` altındaki sinyaller atlanır (loglanır).
- Yatırım tavsiyesi değildir; sorumluluk kullanıcıdadır.

## Uygulama sırası (öneri)

1. models + storage tabloları + RiskManager + PaperExecutor + ExecutionManager + testler.
2. scheduler hook + watch rejim düzeltmesi + `--execute` + paper koşu.
3. `trade` CLI sub-app (status/positions/pending/approve/reject/close/panic).
4. BinanceSpotExecutor (testnet) + testnet e2e.
5. Canlı kilitleri + README/disclaimer güncellemesi + küçük canlı doğrulama (kullanıcıyla).

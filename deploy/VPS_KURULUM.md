# VPS Kurulumu — analizbot'u 7/24 çalıştır + telefondan yönet

Bu rehber, botu **hep açık bir bulut sunucuda (VPS)** çalıştırmanı sağlar. Böylece
bilgisayarın kapalı olsa bile bot çalışır ve sen **telefondan Telegram** ile yönetirsin.

> Önerilen ilk kademe: **paper** (simülasyon). VPS'te de önce paper ile 1-2 gün gözlemle,
> sonra testnet/canlıya geç. Paper'da API anahtarı bile gerekmez — sadece Telegram token.

---

## Genel bakış (6 adım)

1. VPS kirala (Ubuntu 24.04)
2. SSH ile bağlan
3. Kodu çek (`git clone`)
4. Kurulum scriptini çalıştır (`bash deploy/setup.sh`)
5. `.env` dosyasını oluştur
6. Servisi başlat (`systemctl enable --now analizbot`) → telefondan test

---

## Adım 1 — VPS kirala

Bir sağlayıcıdan en küçük Linux sunucusunu kirala:
- **Sağlayıcı:** Hetzner (en ucuz, ~€4/ay), DigitalOcean, Vultr, Contabo...
- **İşletim sistemi:** **Ubuntu 24.04 LTS** (önemli — Python 3.12 hazır gelir).
- **Boyut:** En az **2 GB RAM** (1 GB'de kurulum zorlanır). 1 vCPU yeterli.
- Sunucu oluşunca sana bir **IP adresi** ve **root şifresi** (veya SSH anahtarı) verilir.

## Adım 2 — SSH ile bağlan (Windows)

PowerShell aç ve (IP'yi kendi sunucununkiyle değiştir):
```powershell
ssh root@SUNUCU_IP
```
- İlk bağlantıda "are you sure...?" sorusuna **yes** yaz.
- Şifreni iste(n) irse sağlayıcının verdiği root şifresini gir (yazarken görünmez, normal).
- Artık komutlar **sunucuda** çalışıyor (Windows'ta değil).

## Adım 3 — Kodu çek

> ⚠️ Bu repo herkese açıksa kod görünür; gizli yaptıysan `git clone` için GitHub'da
> oturum açman (token) gerekebilir. Paper'da `.env` zaten sunucuda ayrı oluşturulur.

```bash
git clone https://github.com/Arslan-Emin/analizbot.git
cd analizbot
```

## Adım 4 — Kurulumu çalıştır

```bash
bash deploy/setup.sh
```
Bu; Python venv kurar, bağımlılıkları yükler ve **systemd servisi** oluşturur
(birkaç dakika sürer). Bittiğinde sana sıradaki adımları yazar.

## Adım 5 — `.env` dosyasını oluştur

```bash
nano .env
```
Aşağıyı yapıştır (kendi token/id'inle). Paper için Binance anahtarı GEREKMEZ:
```
BINANCE_API_KEY=
BINANCE_API_SECRET=
BINANCE_TESTNET_API_KEY=
BINANCE_TESTNET_API_SECRET=
LIVE_TRADING=0
TELEGRAM_BOT_TOKEN=senin_token
TELEGRAM_CHAT_ID=senin_chat_id
LOG_LEVEL=INFO
DB_URL=sqlite:///signals.db
```
Kaydet: **Ctrl+O** → Enter, çık: **Ctrl+X**.

> `config.yaml` zaten repoda (paper, confirm, 5 dk). İstersen `nano config.yaml` ile
> `execution.decision: auto` yapabilirsin (sen yokken kendi açsın).

## Adım 6 — Servisi başlat (7/24)

```bash
sudo systemctl enable --now analizbot
```
- `enable` → sunucu yeniden başlasa bile bot otomatik açılır.
- `--now` → hemen başlatır.
- Servis çökerse 10 sn'de bir otomatik yeniden başlar.

**Çalışıyor mu / log izle:**
```bash
systemctl status analizbot      # durum (active/running olmalı)
journalctl -u analizbot -f      # canlı log (Ctrl+C ile çık — bot durmaz)
```

Telefonuna "🤖 analizbot başladı" mesajı gelir. Artık **SSH'ı kapatabilirsin**, bot
sunucuda çalışmaya devam eder. Telefondan `/status`, `/positions` ... yaz.

---

## Günlük kullanım

| İş | Komut (sunucuda) |
|---|---|
| Durdur | `sudo systemctl stop analizbot` |
| Başlat | `sudo systemctl start analizbot` |
| Yeniden başlat | `sudo systemctl restart analizbot` |
| Log izle | `journalctl -u analizbot -f` |
| Kodu güncelle | `git pull && sudo systemctl restart analizbot` |

Telefondan (her yerden): `/status` `/positions` `/pending` `/approve <id>` `/reject <id>`
`/close BTC/USDT` `/panic` `/scan`

---

## Testnet / Canlıya geçiş (sonra)

1. `nano .env` → ilgili anahtarları doldur:
   - Testnet: `BINANCE_TESTNET_API_KEY/SECRET` (testnet.binance.vision).
   - Canlı: `BINANCE_API_KEY/SECRET` + `LIVE_TRADING=1`.
2. `nano config.yaml` → `execution.mode: testnet` (veya `live`).
3. **CANLIDA ÇOK ÖNEMLİ:** Binance API anahtarında **IP whitelist** olarak **VPS'in IP'sini** ekle;
   "Enable Spot Trading" AÇIK, "Withdrawals" KAPALI. `allocation_quote_cap`'i küçük tut.
4. Canlı için servis komutu `--live` ister; canlıda systemd `ExecStart` satırına `--live` ekle:
   `nano /etc/systemd/system/analizbot.service` → `... cli telegram --live` →
   `sudo systemctl daemon-reload && sudo systemctl restart analizbot`.

---

## Sorun giderme

- **Bot mesaj atmıyor:** `journalctl -u analizbot -e` ile son loglara bak. Token/chat_id doğru mu?
- **`active (running)` değil:** `systemctl status analizbot` çıktısındaki hatayı bana gönder.
- **`pip install` çok yavaş/çöküyor:** RAM yetersiz olabilir → 2 GB+ plana çık.
- **Sadece TEK bot çalışmalı:** Hem PC'de hem VPS'te aynı anda çalıştırma (Telegram çakışır).

---

## Güvenlik & maliyet

- `.env` ve `*.db` git'e **girmez** (token/anahtar sızmaz). VPS'te `.env`'i `chmod 600 .env` ile kısıtla.
- Maliyet: sadece VPS kirası (~€4-6/ay). Claude tokenı / paralı API **kullanılmaz**.
- Canlıda risk **işlem kaybıdır** (stop %5 + kill-switch sınırlar); withdrawal kapalı olduğu için
  anahtar sızsa bile para çekilemez.

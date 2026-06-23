#!/usr/bin/env bash
# analizbot — VPS kurulum scripti (Ubuntu 22.04 / 24.04).
#
# KULLANIM (VPS'te, repo klasörünün İÇİNDE):
#   bash deploy/setup.sh
#
# Yaptıkları: sistem paketleri (python venv + git + libgomp) → .venv kurar →
# requirements yükler → systemd servisi (analizbot) oluşturur (otomatik yol/kullanıcı).
# .env'i KURMAZ ve servisi BAŞLATMAZ (onları rehber adımları yapar — secrets güvenliği).
set -euo pipefail

# root isek sudo gereksiz; değilsek sudo kullan (Contabo'da sudo kurulu olmayabilir).
SUDO=""
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; fi

echo "==> Sistem paketleri kuruluyor..."
${SUDO} apt-get update -y
${SUDO} apt-get install -y python3-venv python3-pip git libgomp1

PYVER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "==> Python sürümü: ${PYVER}"
case "${PYVER}" in
  3.12|3.13) : ;;
  *) echo "!! UYARI: Python 3.12+ önerilir (Ubuntu 24.04). Mevcut: ${PYVER}. Devam ediliyor..." ;;
esac

echo "==> Sanal ortam (.venv) + bağımlılıklar kuruluyor (birkaç dakika sürebilir)..."
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

REPO_DIR="$(pwd)"
RUN_USER="$(whoami)"
SERVICE_FILE="/etc/systemd/system/analizbot.service"

echo "==> systemd servisi yazılıyor: ${SERVICE_FILE}"
${SUDO} tee "${SERVICE_FILE}" >/dev/null <<EOF
[Unit]
Description=analizbot Telegram trading bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${REPO_DIR}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONUTF8=1
ExecStart=${REPO_DIR}/.venv/bin/python -m src.app.cli telegram
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

${SUDO} systemctl daemon-reload

cat <<'NEXT'

==> Kurulum tamam. ✅  Şimdi SIRAYLA:

  1) .env dosyasını oluştur (token + ayarlar):
       nano .env
     (İçeriği rehberden/yerel makinenden yapıştır; Ctrl+O kaydet, Ctrl+X çık.)

  2) Botu 7/24 servis olarak başlat:
       sudo systemctl enable --now analizbot

  3) Çalışıyor mu / logları izle:
       systemctl status analizbot
       journalctl -u analizbot -f

  Durdur:   sudo systemctl stop analizbot
  Başlat:   sudo systemctl start analizbot
  Güncelle: git pull && sudo systemctl restart analizbot
NEXT

#!/usr/bin/env bash
# تثبيت وتشغيل لوحة Daytrade على VPS (Ubuntu/Debian)
# انسخ الأمر كله والصقه في Termius مرة واحدة بعد إنشاء السيرفر.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Aied12/us-daytrade-alerts.git}"
APP_DIR="${APP_DIR:-/opt/us-daytrade-alerts}"

echo "==> تحديث النظام وتثبيت Docker"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends ca-certificates curl git docker.io docker-compose-v2
systemctl enable --now docker

echo "==> جلب المشروع إلى $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch origin
  git -C "$APP_DIR" checkout main
  git -C "$APP_DIR" pull --ff-only origin main || git -C "$APP_DIR" reset --hard origin/main
else
  rm -rf "$APP_DIR"
  git clone "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"
mkdir -p data docs pages logs

if [ ! -f .env ]; then
  cp .env.example .env
  # VPS defaults
  {
    echo ""
    echo "# --- VPS ---"
    echo "VPS_CYCLE_SEC=45"
    echo "VPS_PUSH_EVERY=0"
    echo "TELEGRAM_ENABLED=0"
  } >> .env
  echo "==> تم إنشاء .env — عدّل التوكنات لاحقاً إذا احتجت تيليجرام"
fi

echo "==> بناء وتشغيل الحاويات"
cd "$APP_DIR/deploy/vps"
docker compose up -d --build

IP="$(curl -fsS ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')"
echo ""
echo "============================================"
echo " تم التشغيل"
echo " افتح من الآيفون:  http://$IP/"
echo " فحص الصحة:       http://$IP/healthz"
echo "============================================"
echo " أوامر مفيدة:"
echo "   cd $APP_DIR/deploy/vps && docker compose logs -f bot"
echo "   cd $APP_DIR/deploy/vps && docker compose restart"
echo "============================================"

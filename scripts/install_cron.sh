#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN="$ROOT/scripts/run_cron.sh"
BOT="$ROOT/scripts/run_bot_once.sh"
WATCH="$ROOT/scripts/watchdog.sh"
PING="$ROOT/.venv/bin/python $ROOT/scripts/telegram_ping.py"
chmod +x "$RUN" "$BOT" "$WATCH" "$ROOT/scripts/"*.sh "$ROOT/scripts/"*.py 2>/dev/null || true

MARKER_BEGIN="# BEGIN us-daytrade-alerts"
MARKER_END="# END us-daytrade-alerts"

CRON_BLOCK=$(cat <<EOF
$MARKER_BEGIN
SHELL=/bin/bash
PATH=/usr/bin:/bin
# Premarket pack — 11:00 السعودية
0 8 * * 1-5 $RUN premarket
# تحديث السوق كل دقيقة 11ص–11م السعودية
* 8-19 * * 1-5 $RUN tick
0 20 * * 1-5 $RUN tick
# أسعار live.json كل دقيقة — عشان أرقام اللوحة تتحرك
* 7-20 * * 1-5 $ROOT/.venv/bin/python $ROOT/scripts/publish_live.py
# نشر لوحة كامل كل دقيقتين — فرص متحركة
*/2 7-20 * * 1-5 $ROOT/.venv/bin/python $ROOT/scripts/publish_pages.py
# Intel ظهرًا ≈16:00 السعودية
0 13 * * 1-5 $RUN intel
# ملخص مسائي
5 20 * * 1-5 $RUN evening
# اختبار اتصال تيليجرام يومي ≈10:30 ص السعودية
30 7 * * 1-5 $PING
# مستمع أوامر/أزرار كل دقيقة
* * * * * $BOT
# Watchdog كل 10 دقائق
*/10 * * * * $WATCH
$MARKER_END
EOF
)

EXISTING="$(crontab -l 2>/dev/null || true)"
CLEANED="$(printf '%s\n' "$EXISTING" | awk -v b="$MARKER_BEGIN" -v e="$MARKER_END" '
  $0==b {skip=1; next}
  $0==e {skip=0; next}
  !skip {print}
')"

{
  printf '%s\n' "$CLEANED" | sed '/^$/N;/^\n$/D'
  echo
  printf '%s\n' "$CRON_BLOCK"
} | sed '/^$/N;/^\n$/D' | crontab -

echo "✅ تم تثبيت الجدولة:"
crontab -l

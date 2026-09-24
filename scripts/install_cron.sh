#!/usr/bin/env bash
# Cron: every 5 minutes 11:00–23:00 Saudi + telegram bot poller
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN="$ROOT/scripts/run_cron.sh"
BOT="$ROOT/scripts/run_bot_once.sh"
chmod +x "$RUN" "$BOT" "$ROOT/scripts/link_telegram.py" "$ROOT/scripts/scheduled_run.py" "$ROOT/scripts/telegram_bot.py"

MARKER_BEGIN="# BEGIN us-daytrade-alerts"
MARKER_END="# END us-daytrade-alerts"

CRON_BLOCK=$(cat <<EOF
$MARKER_BEGIN
SHELL=/bin/bash
PATH=/usr/bin:/bin
# تحديث السوق كل 5 دقائق 11ص–11م السعودية
*/5 8-19 * * 1-5 $RUN tick
0 20 * * 1-5 $RUN tick
5 20 * * 1-5 $RUN evening
# مستمع أوامر/أزرار تيليجرام كل دقيقة
* * * * * $BOT
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

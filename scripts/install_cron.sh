#!/usr/bin/env bash
# Cron: every 5 minutes from 11:00 to 23:00 Saudi (AST = UTC+3).
# 11:00 SA = 08:00 UTC … 23:00 SA = 20:00 UTC
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN="$ROOT/scripts/run_cron.sh"
chmod +x "$RUN" "$ROOT/scripts/link_telegram.py" "$ROOT/scripts/scheduled_run.py"

MARKER_BEGIN="# BEGIN us-daytrade-alerts"
MARKER_END="# END us-daytrade-alerts"

CRON_BLOCK=$(cat <<EOF
$MARKER_BEGIN
SHELL=/bin/bash
PATH=/usr/bin:/bin
# كل 5 دقائق من 11:00 ص إلى 11:00 م بتوقيت السعودية
*/5 8-19 * * 1-5 $RUN tick
0 20 * * 1-5 $RUN tick
# ملخص مسائي إضافي بعد آخر تحديث
5 20 * * 1-5 $RUN evening
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

echo "✅ تم تثبيت الجدولة (كل 5 دقائق 11ص–11م السعودية):"
crontab -l
echo
echo "السجلات: $ROOT/logs/cron_*.log"

#!/usr/bin/env bash
# Install / refresh crontab for US session alerts (times in UTC).
# Windows map (EDT = UTC-4, typical Mar–Nov):
#   Morning  13:05 UTC ≈ 09:05 ET ≈ 16:05 الرياض
#   Intraday every 15m 13:45–19:45 UTC
#   Evening  20:15 UTC ≈ 16:15 ET ≈ 23:15 الرياض
# During EST (UTC-5) times shift +1h — scheduled_run.py still gates by NY clock.
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
# Morning brief — weekdays ~09:05 ET (16:05 الرياض صيفًا)
5 13 * * 1-5 $RUN morning
# First intraday check ~09:45 ET
45 13 * * 1-5 $RUN auto
# Intraday every 15 min 10:00–15:45 ET
0,15,30,45 14-19 * * 1-5 $RUN auto
# Evening summary — ~16:15 ET (23:15 الرياض صيفًا)
15 20 * * 1-5 $RUN evening
$MARKER_END
EOF
)

EXISTING="$(crontab -l 2>/dev/null || true)"
# Strip previous block if present
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
echo
echo "السجلات: $ROOT/logs/cron_*.log"
echo "لربط تيليجرام:"
echo "  cd $ROOT && source .venv/bin/activate"
echo "  python scripts/link_telegram.py --token 'YOUR_BOT_TOKEN'"

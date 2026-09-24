#!/usr/bin/env bash
# Install / refresh crontab for US session alerts (times in UTC).
# Saudi (AST=UTC+3) mapping during US daylight (EDT=UTC-4):
#   Premarket brief  08:00 UTC = 11:00 الرياض = 04:00 ET (pre-market open)
#   Cash open remind 13:05 UTC = 16:05 الرياض = 09:05 ET
#   Intraday         13:45–19:45 UTC during cash session
#   Evening          20:15 UTC = 23:15 الرياض = 16:15 ET
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
# Premarket opportunities — 11:00 صباحًا السعودية
0 8 * * 1-5 $RUN morning
# Reminder before cash open — ≈16:05 السعودية / 09:05 ET
5 13 * * 1-5 $RUN morning
# First intraday check ~09:45 ET
45 13 * * 1-5 $RUN auto
# Intraday every 15 min during cash session
0,15,30,45 14-19 * * 1-5 $RUN auto
# Evening summary — ≈23:15 السعودية
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

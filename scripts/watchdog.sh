#!/usr/bin/env bash
# 84 watchdog — restart bot poller / ensure cron alive
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/logs/watchdog.log"
mkdir -p "$ROOT/logs"

{
  echo "==== $(date -u -Iseconds) watchdog ===="
  if ! pgrep -x cron >/dev/null 2>&1; then
    echo "cron down — starting"
    sudo service cron start || service cron start || true
  else
    echo "cron ok"
  fi

  # heartbeat via status file age
  STATUS="$ROOT/data/status.json"
  if [[ -f "$STATUS" ]]; then
    AGE=$(( $(date +%s) - $(stat -c %Y "$STATUS") ))
    echo "status age=${AGE}s"
    if (( AGE > 900 )); then
      echo "stale status — forcing tick"
      "$ROOT/.venv/bin/python" "$ROOT/scripts/scheduled_run.py" --mode tick --force || true
    fi
  else
    echo "no status yet — creating"
    "$ROOT/.venv/bin/python" - <<'PY'
from bot.ops import touch_status
touch_status(ok=True, source="watchdog-init")
PY
  fi

  # one bot poll
  "$ROOT/scripts/run_bot_once.sh" || true
} >>"$LOG" 2>&1

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

  # heartbeat via published dashboard (docs) — what the user actually sees
  STATUS="$ROOT/docs/status.json"
  DATA_STATUS="$ROOT/data/status.json"
  AGE=-1
  if [[ -f "$STATUS" ]]; then
    AGE=$(( $(date +%s) - $(stat -c %Y "$STATUS") ))
    echo "docs status age=${AGE}s"
  elif [[ -f "$DATA_STATUS" ]]; then
    AGE=$(( $(date +%s) - $(stat -c %Y "$DATA_STATUS") ))
    echo "data status age=${AGE}s (docs missing)"
  else
    echo "no status yet — creating"
    "$ROOT/.venv/bin/python" - <<'PY'
from bot.ops import touch_status
touch_status(ok=True, source="watchdog-init")
PY
    AGE=99999
  fi

  # >15 min: force market tick + rebuild/push Pages (VM may have slept)
  if (( AGE < 0 || AGE > 900 )); then
    echo "stale dashboard — forcing tick + publish_pages"
    "$ROOT/.venv/bin/python" "$ROOT/scripts/scheduled_run.py" --mode tick --force || true
    "$ROOT/.venv/bin/python" "$ROOT/scripts/publish_pages.py" || true
  fi

  # one bot poll
  "$ROOT/scripts/run_bot_once.sh" || true
} >>"$LOG" 2>&1

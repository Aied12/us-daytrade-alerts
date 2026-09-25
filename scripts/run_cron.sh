#!/usr/bin/env bash
# Wrapper for cron: loads .env and runs the alert bot.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export PATH="$ROOT/.venv/bin:/usr/bin:/bin"
PYTHON="$ROOT/.venv/bin/python"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

MODE="${1:-auto}"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
LOG_FILE="$LOG_DIR/cron_${MODE}_${STAMP}.log"

{
  echo "==== $(date -u -Iseconds) mode=$MODE ===="
  if [[ ! -x "$PYTHON" ]]; then
    echo "ERROR: venv python missing at $PYTHON"
    exit 1
  fi
  # shellcheck disable=SC1091
  set -a
  [[ -f "$ROOT/.env" ]] && source "$ROOT/.env"
  set +a
  "$PYTHON" "$ROOT/scripts/scheduled_run.py" --mode "$MODE"
  # Refresh GitHub Pages JSON after market scans so the dashboard auto-updates
  # Skip on every tick — dedicated */2 publish_pages cron owns the push (avoids pile-ups)
  if [[ "$MODE" == "premarket" || "$MODE" == "morning" || "$MODE" == "evening" || "$MODE" == "intel" ]]; then
    echo "==== publish pages $(date -u -Iseconds) ===="
    "$PYTHON" "$ROOT/scripts/publish_pages.py" || echo "publish_pages failed (non-fatal)"
  fi
  echo "==== done $(date -u -Iseconds) ===="
} >>"$LOG_FILE" 2>&1

# Keep last 40 cron logs
ls -1t "$LOG_DIR"/cron_*.log 2>/dev/null | tail -n +41 | xargs -r rm -f

#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
LOG="$ROOT/logs/bot_poller.log"
mkdir -p "$ROOT/logs"
{
  echo "==== $(date -u -Iseconds) bot poll ===="
  "$ROOT/.venv/bin/python" "$ROOT/scripts/telegram_bot.py" --once
} >>"$LOG" 2>&1

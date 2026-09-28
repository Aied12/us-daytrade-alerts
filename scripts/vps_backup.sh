#!/usr/bin/env bash
# Daily backup of VPS daytrade data/config (keep 7 days).
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/us-daytrade-alerts}"
BACKUP_ROOT="${BACKUP_ROOT:-/opt/backups/us-daytrade-alerts}"
KEEP_DAYS="${KEEP_DAYS:-7}"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
DEST="$BACKUP_ROOT/$STAMP"

mkdir -p "$DEST" "$BACKUP_ROOT"
cd "$APP_DIR"

# Exclude huge tick dumps and cache noise
tar -czf "$DEST/data.tgz" \
  --exclude='data/cache' \
  --exclude='data/tick_*.json' \
  --exclude='data/publish_pages.lock' \
  data 2>/dev/null || true

# Day performance exports (appear → open → close analysis)
tar -czf "$DEST/day-performance.tgz" \
  data/day_performance data/lifecycle \
  docs/day-performance.json docs/day-performance.csv \
  2>/dev/null || true

tar -czf "$DEST/docs-json.tgz" \
  docs/status.json docs/prices-live.json docs/live.json docs/news-live.json \
  docs/index.html docs/sw.js docs/manifest.webmanifest \
  2>/dev/null || true

# .env backup (contains secrets — keep on server only)
if [ -f .env ]; then
  cp -a .env "$DEST/env.backup"
  chmod 600 "$DEST/env.backup"
fi

# Compose / deploy snapshot
tar -czf "$DEST/deploy.tgz" deploy/vps 2>/dev/null || true

# Record git tip
git rev-parse HEAD > "$DEST/git-head.txt" 2>/dev/null || true
date -u -Iseconds > "$DEST/created_at.txt"

# Prune old backups
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +"$KEEP_DAYS" -exec rm -rf {} + 2>/dev/null || true

SIZE="$(du -sh "$DEST" 2>/dev/null | awk '{print $1}')"
echo "[backup] ok $DEST size=$SIZE keep=${KEEP_DAYS}d"

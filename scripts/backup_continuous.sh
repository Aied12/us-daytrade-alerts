#!/usr/bin/env bash
# Continuous backup for us-daytrade-alerts:
# 1) Local timestamped tarball under /opt/backups/us-daytrade-alerts/snapshots/
# 2) Git commit on branch backup/vps-live via a dedicated worktree
# 3) Push to GitHub when GITHUB_TOKEN / GH_TOKEN / GITHUB_PAT is set in .env
set -euo pipefail

ROOT="${ROOT:-/opt/us-daytrade-alerts}"
BACKUP_ROOT="${BACKUP_ROOT:-/opt/backups/us-daytrade-alerts}"
BRANCH="${BACKUP_BRANCH:-backup/vps-live}"
WT="${BACKUP_ROOT}/git-wt"
KEEP_LOCAL="${BACKUP_KEEP:-48}"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
LOG_DIR="${BACKUP_ROOT}/logs"
STATUS_FILE="${BACKUP_ROOT}/LAST_BACKUP.txt"
mkdir -p "$BACKUP_ROOT/snapshots" "$LOG_DIR"
LOG="$LOG_DIR/backup_${STAMP}.log"
exec >>"$LOG" 2>&1

echo "=== backup start ${STAMP} ==="
cd "$ROOT"

TOKEN=""
if [[ -f "$ROOT/.env" ]]; then
  while IFS= read -r line; do
    case "$line" in
      GITHUB_TOKEN=*|GH_TOKEN=*|GITHUB_PAT=*)
        key="${line%%=*}"
        val="${line#*=}"
        val="${val%\"}"; val="${val#\"}"
        val="${val%\'}"; val="${val#\'}"
        export "$key=$val"
        ;;
    esac
  done < <(grep -E '^(GITHUB_TOKEN|GH_TOKEN|GITHUB_PAT)=' "$ROOT/.env" 2>/dev/null || true)
  TOKEN="${GITHUB_TOKEN:-${GH_TOKEN:-${GITHUB_PAT:-}}}"
fi

# --- 1) Local snapshot ---
SNAP_NAME="snap_${STAMP}"
SNAP_DIR="$BACKUP_ROOT/snapshots/${SNAP_NAME}"
mkdir -p "$SNAP_DIR"
rsync -a \
  --exclude '.git/' \
  --exclude 'data/cache/' \
  --exclude 'data/media/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude 'node_modules/' \
  "$ROOT/bot" "$ROOT/scripts" "$ROOT/pages" "$ROOT/docs" "$ROOT/tests" \
  "$SNAP_DIR/" 2>/dev/null || true
mkdir -p "$SNAP_DIR/data"
rsync -a \
  --exclude 'cache/' \
  --exclude 'media/' \
  "$ROOT/data/" "$SNAP_DIR/data/" 2>/dev/null || true
if [[ -f "$ROOT/.env" ]]; then
  python3 - <<'PY' "$ROOT/.env" "$SNAP_DIR/.env.redacted"
import sys
src, dst = sys.argv[1], sys.argv[2]
out = []
for line in open(src, encoding="utf-8", errors="ignore"):
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        out.append(f"{k.strip()}=***REDACTED***\n" if v.strip() else line)
    else:
        out.append(line)
open(dst, "w", encoding="utf-8").writelines(out)
PY
fi
tar -C "$BACKUP_ROOT/snapshots" -czf "$BACKUP_ROOT/snapshots/${SNAP_NAME}.tgz" "$SNAP_NAME"
rm -rf "$SNAP_DIR"
ls -1t "$BACKUP_ROOT/snapshots"/snap_*.tgz 2>/dev/null | tail -n +"$((KEEP_LOCAL + 1))" | xargs -r rm -f
echo "local snapshot ok: ${SNAP_NAME}.tgz"

GITHUB_OK="no"
GIT_OK="no"

# --- 2) Git worktree backup ---
if [[ -d "$ROOT/.git" ]]; then
  git config user.email "backup@us-daytrade-alerts.local"
  git config user.name "VPS Backup Bot"
  if [[ ! -d "$WT/.git" && ! -f "$WT/.git" ]]; then
    rm -rf "$WT"
    git worktree prune >/dev/null 2>&1 || true
    git branch -f "$BRANCH" HEAD >/dev/null 2>&1 || true
    git worktree add -f -B "$BRANCH" "$WT" HEAD
  else
    git -C "$WT" checkout -B "$BRANCH" >/dev/null 2>&1 || true
  fi

  # Mirror selected live paths into worktree
  rsync -a --delete \
    --exclude '__pycache__/' --exclude '*.pyc' \
    "$ROOT/bot/" "$WT/bot/"
  rsync -a --delete "$ROOT/scripts/" "$WT/scripts/"
  rsync -a --delete "$ROOT/pages/" "$WT/pages/"
  rsync -a --delete "$ROOT/docs/" "$WT/docs/"
  rsync -a --delete "$ROOT/tests/" "$WT/tests/" 2>/dev/null || true
  mkdir -p "$WT/data/day_performance" "$WT/data/lifecycle"
  for f in strategy_ledger.json strategy_performance.json qannas_board.json sniper_board.json jamal_board.json; do
    [[ -f "$ROOT/data/$f" ]] && cp -a "$ROOT/data/$f" "$WT/data/$f" || true
  done
  rsync -a "$ROOT/data/day_performance/" "$WT/data/day_performance/" 2>/dev/null || true
  rsync -a "$ROOT/data/lifecycle/" "$WT/data/lifecycle/" 2>/dev/null || true

  git -C "$WT" add -A bot scripts pages docs tests data 2>/dev/null || true
  git -C "$WT" reset -q -- .env data/cache data/media 2>/dev/null || true
  if git -C "$WT" diff --cached --quiet; then
    echo "git: nothing new to commit"
    GIT_OK="unchanged"
  else
    git -C "$WT" commit -m "backup(vps): ${STAMP} auto snapshot" >/dev/null
    echo "git: committed on ${BRANCH}"
    GIT_OK="committed"
  fi

  # --- 3) Push ---
  REMOTE_URL="$(git -C "$ROOT" remote get-url origin 2>/dev/null || true)"
  if [[ -z "$REMOTE_URL" ]]; then
    echo "git: no origin remote — skip push"
  elif [[ -z "$TOKEN" ]]; then
    echo "git: GITHUB_TOKEN missing in .env — local+git only (no GitHub push yet)"
  else
    push_url="$(python3 - <<'PY' "$REMOTE_URL" "$TOKEN"
import sys
url, token = sys.argv[1], sys.argv[2]
if url.startswith("https://"):
    rest = url.split("https://", 1)[1]
    if "@" in rest:
        rest = rest.split("@", 1)[1]
    print("https://x-access-token:" + token + "@" + rest)
else:
    print(url)
PY
)"
    if git -C "$WT" push --force-with-lease "$push_url" "HEAD:refs/heads/${BRANCH}"; then
      echo "git: pushed ${BRANCH} to GitHub"
      GITHUB_OK="yes"
    elif git -C "$WT" push -u "$push_url" "HEAD:refs/heads/${BRANCH}"; then
      echo "git: pushed ${BRANCH} to GitHub (init)"
      GITHUB_OK="yes"
    else
      echo "git: GitHub push FAILED"
      GITHUB_OK="fail"
    fi
  fi
else
  echo "no git repo — skip git/github sync"
fi

{
  echo "stamp=${STAMP}"
  echo "local_tarball=${SNAP_NAME}.tgz"
  echo "git=${GIT_OK}"
  echo "github_push=${GITHUB_OK}"
  echo "branch=${BRANCH}"
  echo "repo=https://github.com/Aied12/us-daytrade-alerts/tree/${BRANCH}"
} >"$STATUS_FILE"

# prune old logs (keep 30 days)
find "$LOG_DIR" -type f -name 'backup_*.log' -mtime +30 -delete 2>/dev/null || true
echo "=== backup done ${STAMP} github_push=${GITHUB_OK} ==="

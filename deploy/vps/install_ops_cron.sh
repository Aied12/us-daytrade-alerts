#!/usr/bin/env bash
# Install host cron for backup + watchdog on the VPS.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/us-daytrade-alerts}"
CRON_FILE="/etc/cron.d/us-daytrade-ops"

chmod +x "$APP_DIR/scripts/vps_backup.sh" "$APP_DIR/scripts/vps_watchdog.py" || true
mkdir -p /opt/backups/us-daytrade-alerts "$APP_DIR/logs"

# Watchdog every 3 minutes; backup daily 02:15 UTC (~05:15 Riyadh)
cat > "$CRON_FILE" <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
APP_DIR=$APP_DIR

*/3 * * * * root cd \$APP_DIR && /usr/bin/python3 scripts/vps_watchdog.py >> logs/vps_watchdog.log 2>&1
15 2 * * * root APP_DIR=\$APP_DIR /bin/bash \$APP_DIR/scripts/vps_backup.sh >> \$APP_DIR/logs/vps_backup.log 2>&1
EOF
chmod 644 "$CRON_FILE"

# Ensure cron running
systemctl enable --now cron 2>/dev/null || service cron start 2>/dev/null || true

echo "[ops-cron] installed $CRON_FILE"
echo "  - watchdog every 3 min"
echo "  - backup daily 02:15 UTC"
# Smoke once (append to the same logs cron uses)
/usr/bin/python3 "$APP_DIR/scripts/vps_watchdog.py" >> "$APP_DIR/logs/vps_watchdog.log" 2>&1 || true
APP_DIR="$APP_DIR" /bin/bash "$APP_DIR/scripts/vps_backup.sh" >> "$APP_DIR/logs/vps_backup.log" 2>&1 || true
ls -la /opt/backups/us-daytrade-alerts | tail -5
echo "[ops-cron] logs: $APP_DIR/logs/vps_watchdog.log $APP_DIR/logs/vps_backup.log"

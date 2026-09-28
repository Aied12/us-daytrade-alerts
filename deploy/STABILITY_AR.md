# استقرار وتشغيل VPS

## ما تم تفعيله

1. **إيقاف GitHub Actions المقرر**
   - الجداول (`schedule`) محذوفة من `always-on.yml` و `alerts.yml`
   - الـ workflows معطّلة يدوياً في GitHub (`disabled_manually`) حتى لا تستهلك رصيد أو تتعارض مع الـ VPS
   - التحديث الحي من الـ VPS فقط

2. **نسخة احتياطية يومية**
   - السكربت: `scripts/vps_backup.sh`
   - يحفظ في: `/opt/backups/us-daytrade-alerts/`
   - يحتفظ بـ **7 أيام**
   - يشمل: `data/` (بدون cache/ticks) + JSON اللوحة + `.env` + deploy

3. **مراقبة توقف الفحص**
   - السكربت: `scripts/vps_watchdog.py`
   - كل **3 دقائق**
   - إذا `status.json` أقدم من ~3 دقائق أو حاوية واقفة:
     - يحاول إعادة تشغيل Docker
     - يرسل تنبيه Web Push + تيليجرام (إذا التوكن موجود)

## تثبيت الكرون على السيرفر

```bash
bash /opt/us-daytrade-alerts/deploy/vps/install_ops_cron.sh
```

## تنبيه مهم للإشعارات

حتى يوصلك تنبيه «توقف الفحص» على تيليجرام، لازم في `.env`:

```bash
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

(الـ Watchdog يرسل حتى لو `TELEGRAM_ENABLED=0`)

وWeb Push يحتاج تفعيل 🔔 إشعار من التطبيق مرة واحدة.

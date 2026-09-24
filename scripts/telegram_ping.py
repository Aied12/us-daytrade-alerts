#!/usr/bin/env python3
"""88 Daily Telegram connectivity test + 92 status helper."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.backup import backup_settings, mask_token
from bot.config import load_settings
from bot.holidays import holiday_note, is_trading_day
from bot.notify import send_telegram
from bot.ops import read_status, recent_errors, touch_status
import requests


def ping() -> bool:
    settings = load_settings()
    if not settings.telegram_bot_token:
        touch_status(ok=False, telegram="no-token")
        return False
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe",
            timeout=20,
        )
        data = r.json()
        ok = bool(data.get("ok"))
    except Exception as e:
        touch_status(ok=False, telegram=str(e))
        return False

    now = datetime.now(settings.local_tz).strftime("%Y-%m-%d %H:%M %Z")
    note = holiday_note() or "يوم تداول عادي" if is_trading_day() else "ليس يوم تداول"
    msg = (
        f"✅ اختبار اتصال يومي\n"
        f"الوقت: {now}\n"
        f"البوت: @{data.get('result', {}).get('username', '?')}\n"
        f"التوكن: {mask_token(settings.telegram_bot_token)}\n"
        f"المستخدمون: {len(settings.all_private_chat_ids)}\n"
        f"LIGHT_MODE={settings.light_mode} API_THRIFT={settings.api_thrift}\n"
        f"السوق: {note}"
    )
    sent = send_telegram(settings, msg)
    touch_status(ok=ok and sent, telegram="ping-ok" if sent else "ping-send-failed", source="telegram_ping")
    backup_settings(settings)
    return ok and sent


def status_text() -> str:
    settings = load_settings()
    st = read_status()
    errs = recent_errors(2)
    lines = [
        "🩺 /status",
        f"telegram: {'OK' if settings.telegram_enabled else 'OFF'}",
        f"users: {len(settings.all_private_chat_ids)}",
        f"mode: {settings.user_mode}",
        f"tz: {settings.timezone_name}",
        f"light: {settings.light_mode} thrift: {settings.api_thrift}",
        f"trading_day: {is_trading_day()}",
        f"last_status: {st}",
    ]
    if errs:
        lines.append("آخر خطأ:")
        lines.append(errs[-1][:400])
    return "\n".join(lines)


if __name__ == "__main__":
    if "--status-only" in sys.argv:
        print(status_text())
    else:
        ok = ping()
        print("ping", ok)
        sys.exit(0 if ok else 1)

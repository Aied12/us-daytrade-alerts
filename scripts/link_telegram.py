#!/usr/bin/env python3
"""Link Telegram bot: save token/chat_id to .env and send a test message."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"


def upsert_env(key: str, value: str) -> None:
    text = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.M)
    line = f"{key}={value}"
    if pattern.search(text):
        text = pattern.sub(line, text)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += line + "\n"
    ENV_PATH.write_text(text, encoding="utf-8")


def get_me(token: str) -> dict:
    r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data)
    return data["result"]


def get_updates(token: str) -> list:
    r = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        params={"limit": 20, "timeout": 0},
        timeout=25,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data)
    return data.get("result") or []


def pick_chat_id(updates: list) -> str | None:
    for upd in reversed(updates):
        msg = upd.get("message") or upd.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if "id" in chat:
            return str(chat["id"])
    return None


def send_test(token: str, chat_id: str) -> None:
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": (
                "✅ تم ربط بوت تنبيهات الأسهم الأمريكية بنجاح.\n"
                "الوضع: تنبيهات فقط — بدون تنفيذ أوامر.\n"
                "رأس المال: 45,000 ر.س | مخاطرة الصفقة 1% | حد يومي 2%."
            ),
        },
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data)


def main() -> None:
    p = argparse.ArgumentParser(description="ربط تيليجرام لمساعد التنبيهات")
    p.add_argument("--token", required=True, help="توكن البوت من @BotFather")
    p.add_argument(
        "--chat-id",
        default="",
        help="اختياري إذا تعرفه؛ وإلا يُستخرج بعد ما تراسل البوت",
    )
    args = p.parse_args()
    token = args.token.strip()

    print("التحقق من التوكن...")
    me = get_me(token)
    print(f"البوت: @{me.get('username')} ({me.get('first_name')})")

    chat_id = args.chat_id.strip()
    if not chat_id:
        print("جاري البحث عن chat_id من آخر رسالة...")
        updates = get_updates(token)
        chat_id = pick_chat_id(updates) or ""
        if not chat_id:
            print(
                "\n❌ ما لقيت محادثة بعد.\n"
                "1) افتح البوت في تيليجرام واضغط Start / أرسل أي رسالة\n"
                "2) شغّل نفس الأمر مرة ثانية\n"
                f"   python scripts/link_telegram.py --token '{token}'\n"
            )
            sys.exit(2)

    print(f"chat_id = {chat_id}")
    print("إرسال رسالة تجريبية...")
    send_test(token, chat_id)

    upsert_env("TELEGRAM_BOT_TOKEN", token)
    upsert_env("TELEGRAM_CHAT_ID", chat_id)
    print(f"\n✅ تم الحفظ في {ENV_PATH}")
    print("الجدولة اليومية جاهزة — التنبيهات بتروح لتيليجرام تلقائيًا.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Telegram command + button handler (long poll)."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.charts import make_daily_chart, make_market_poster
from bot.config import load_settings
from bot.extras import (
    format_after_hours,
    format_fed_calendar,
    format_premarket_hotlist,
    format_sector_etfs,
    format_stock_news,
    format_style_board,
)
from bot.formatters import action_keyboard, mode_keyboard, format_signal_card, is_urgent
from bot.journal import log_action, summarize_journal
from bot.market_data import market_context, scan_watchlist
from bot.notify import (
    answer_callback,
    deliver,
    send_photo,
    send_telegram,
    send_voice,
    set_bot_commands,
)
from bot.reports import build_full_pack
from bot.risk import plan_trade, risk_banner
from bot.signals import Action, compare_strategies
from bot.voice import synthesize_arabic, voice_script_from_update

RIYADH = ZoneInfo("Asia/Riyadh")
OFFSET_FILE = ROOT / "data" / "tg_offset.txt"


def _api(settings, method: str) -> str:
    return f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"


def load_offset() -> int:
    if OFFSET_FILE.exists():
        try:
            return int(OFFSET_FILE.read_text().strip() or "0")
        except Exception:
            return 0
    return 0


def save_offset(value: int) -> None:
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(str(value), encoding="utf-8")


def set_mode(mode: str) -> None:
    env = ROOT / ".env"
    text = env.read_text(encoding="utf-8") if env.exists() else ""
    line = f"USER_MODE={mode}"
    if re.search(r"^USER_MODE=.*$", text, re.M):
        text = re.sub(r"^USER_MODE=.*$", line, text, flags=re.M)
    else:
        text = text.rstrip() + "\n" + line + "\n"
    env.write_text(text, encoding="utf-8")


def cmd_help(settings) -> str:
    return (
        "أوامر البوت:\n"
        "/scan — فحص الآن\n"
        "/premarket — قائمة ساخنة قبل الافتتاح\n"
        "/afterhours — ملخص بعد الإغلاق\n"
        "/fed — تقويم الفيدرالي\n"
        "/news — أخبار عاجلة\n"
        "/sectors — ETF القطاعات\n"
        "/style — نمو vs قيمة\n"
        "/status — هل البوت حي؟\n"
        "/risk /journal /mode /chart /voice /strategies\n"
        "/help — هذه القائمة\n\n"
        f"الوضع الحالي: {'مبتدئ' if settings.is_beginner else 'محترف'}\n"
        f"فلتر السعر: فوق ${settings.min_price_usd:g}\n"
        "الأزرار: دخلت / راقبت / تجاهلت"
    )


def handle_scan(settings) -> None:
    snaps = scan_watchlist(settings.watchlist)
    snaps = [s for s in snaps if s.last >= settings.min_price_usd]
    pack = build_full_pack(settings, snaps)
    actionable = [
        s
        for s in pack["signals"]
        if s.action in (Action.CONSIDER_LONG, Action.WATCH_ENTRY)
        and getattr(s, "side", None) != "short"
        and s.symbol != "MARKET"
    ][:5]
    if not actionable:
        deliver(settings, "📡 /scan", "لا توجد فرص قوية فوق فلتر السعر الآن.")
        return
    for sig in actionable:
        plan = plan_trade(settings, sig)
        card = format_signal_card(settings, sig, plan)
        if is_urgent(settings, sig):
            card = "🚨 عاجل — فرصة قوية\n" + card
        send_telegram(
            settings,
            card,
            reply_markup=action_keyboard(sig.symbol),
            also_channel=bool(settings.telegram_channel_id),
        )
    deliver(settings, "📡 /scan", f"تم إرسال {len(actionable)} فرصة مع أزرار القرار.")


def handle_chart(settings, symbol: str | None = None) -> None:
    media = settings.data_dir / "media"
    if symbol:
        path = media / f"chart_{symbol.upper()}.png"
        out = make_daily_chart(symbol.upper(), path)
        caption = f"شارت {symbol.upper()}"
    else:
        path = media / f"poster_{datetime.now(RIYADH).strftime('%Y%m%d')}.png"
        out = make_market_poster(path)
        caption = "ملصق السوق اليومي"
    if not out:
        deliver(settings, "🖼 chart", "تعذّر إنشاء الرسم.")
        return
    ok = send_photo(settings, out, caption=caption, also_channel=True)
    deliver(settings, "🖼 chart", "تم إرسال الملصق." if ok else "فشل إرسال الصورة.")


def handle_voice(settings) -> None:
    ctx = market_context()
    snaps = scan_watchlist(settings.watchlist[:12])
    pack = build_full_pack(settings, snaps)
    top = [
        s.symbol
        for s in pack["signals"]
        if s.action in (Action.CONSIDER_LONG, Action.WATCH_ENTRY)
    ][:3]
    text = (
        f"ملخص سريع. مزاج السوق: {ctx.get('tone', 'غير واضح')}. "
        f"أبرز الفرص: {' و '.join(top) if top else 'لا توجد فرص قوية الآن'}."
    )
    out = settings.data_dir / "media" / "voice_summary.mp3"
    path = synthesize_arabic(text, out)
    if not path:
        deliver(settings, "🎙️ voice", text + "\n(تعذّر توليد الصوت — هذا النص بديل)")
        return
    ok = send_voice(settings, path, caption="ملخص صوتي قصير")
    print("[voice]", ok)


def handle_callback(settings, cq: dict) -> None:
    data = cq.get("data") or ""
    cid = cq.get("id")
    parts = data.split(":")
    if len(parts) >= 3 and parts[0] == "act":
        action, symbol = parts[1], parts[2]
        labels = {"entered": "دخلت", "watching": "راقبت", "ignored": "تجاهلت"}
        log_action(settings, symbol, action)
        answer_callback(settings, cid, f"تم تسجيل: {labels.get(action, action)} {symbol}")
        send_telegram(
            settings,
            f"📒 تم: {labels.get(action, action)} — {symbol}\n"
            f"الوقت: {datetime.now(RIYADH).strftime('%H:%M')}",
        )
    elif len(parts) == 2 and parts[0] == "mode":
        mode = parts[1]
        set_mode(mode)
        answer_callback(settings, cid, f"الوضع: {mode}")
        send_telegram(
            settings,
            f"✅ تم تعديل الوضع إلى: {'مبتدئ' if mode == 'beginner' else 'محترف'}\n"
            "التحديث الجاي يستخدم الأسلوب الجديد.",
        )
    else:
        answer_callback(settings, cid, "تم")


def handle_message(settings, msg: dict) -> None:
    text = (msg.get("text") or "").strip()
    if not text.startswith("/"):
        return
    cmd = text.split()[0].split("@")[0].lower()
    arg = text.split()[1] if len(text.split()) > 1 else None

    if cmd in ("/start", "/help"):
        send_telegram(settings, cmd_help(settings))
    elif cmd == "/scan":
        handle_scan(settings)
    elif cmd == "/risk":
        send_telegram(settings, risk_banner(load_settings()))
    elif cmd == "/journal":
        send_telegram(settings, summarize_journal(settings))
    elif cmd == "/mode":
        send_telegram(
            settings,
            f"الوضع الحالي: {'مبتدئ' if settings.is_beginner else 'محترف'}\nاختر:",
            reply_markup=mode_keyboard(),
        )
    elif cmd == "/premarket":
        deliver(settings, "🌅 Premarket", format_premarket_hotlist(settings), also_channel=True)
    elif cmd == "/afterhours":
        deliver(settings, "🌙 After-hours", format_after_hours(settings), also_channel=True)
    elif cmd == "/fed":
        deliver(settings, "🏛 Fed", format_fed_calendar(), also_channel=True)
    elif cmd == "/news":
        deliver(settings, "📰 News", format_stock_news(settings), also_channel=True)
    elif cmd == "/sectors":
        deliver(settings, "🧭 Sectors", format_sector_etfs(), also_channel=True)
    elif cmd == "/style":
        deliver(settings, "🌱🏦 Style", format_style_board(settings), also_channel=True)
    elif cmd == "/status":
        from bot.backup import backup_settings, mask_token
        from bot.holidays import holiday_note, is_trading_day
        from bot.ops import read_status, recent_errors

        st = read_status()
        errs = recent_errors(1)
        msg = (
            f"🩺 /status\n"
            f"telegram: {'OK' if settings.telegram_enabled else 'OFF'}\n"
            f"users: {len(settings.all_private_chat_ids)}\n"
            f"token: {mask_token(settings.telegram_bot_token)}\n"
            f"mode: {settings.user_mode}\n"
            f"tz: {settings.timezone_name}\n"
            f"light: {settings.light_mode} thrift: {settings.api_thrift}\n"
            f"trading_day: {is_trading_day()}\n"
            f"note: {holiday_note() or '-'}\n"
            f"last: {st.get('updated_at', '-')} ok={st.get('ok')}\n"
        )
        if errs:
            msg += "\nآخر خطأ:\n" + errs[-1][:350]
        send_telegram(settings, msg)
        backup_settings(settings)
    elif cmd == "/strategies":
        # /strategies فجوة|كسر  or defaults
        raw = text[len("/strategies"):].strip()
        if "|" in raw:
            a, b = [x.strip() for x in raw.split("|", 1)]
        else:
            a, b = "كسر قمة 20 يوم", "حجم غير طبيعي صاعد"
        send_telegram(settings, compare_strategies(settings, a, b))
    elif cmd == "/chart":
        handle_chart(settings, arg)
    elif cmd == "/voice":
        handle_voice(settings)
    else:
        send_telegram(settings, "أمر غير معروف. /help")


def poll_once(settings, timeout: int = 25) -> None:
    offset = load_offset()
    resp = requests.get(
        _api(settings, "getUpdates"),
        params={"offset": offset, "timeout": timeout, "allowed_updates": json.dumps(["message", "callback_query"])},
        timeout=timeout + 10,
    )
    data = resp.json()
    if not data.get("ok"):
        print("[bot] getUpdates failed", data)
        return
    for upd in data.get("result") or []:
        save_offset(upd["update_id"] + 1)
        if "callback_query" in upd:
            handle_callback(settings, upd["callback_query"])
        elif "message" in upd:
            handle_message(settings, upd["message"])


def main() -> None:
    settings = load_settings()
    if not settings.telegram_enabled:
        print("telegram not configured")
        sys.exit(1)
    set_bot_commands(settings)
    # once = single poll (for cron); loop = long running
    once = "--once" in sys.argv
    print(f"[bot] start once={once}")
    if once:
        poll_once(settings, timeout=5)
        return
    while True:
        try:
            settings = load_settings()
            poll_once(settings, timeout=25)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print("[bot] error", e)
            time.sleep(3)


if __name__ == "__main__":
    main()

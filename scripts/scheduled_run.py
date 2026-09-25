#!/usr/bin/env python3
"""Scheduled runner — 5-minute ticks from 11:00 to 23:00 Saudi time."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.charts import make_market_poster
from bot.config import load_settings
from bot.extras import (
    format_after_hours,
    format_fed_calendar,
    format_premarket_hotlist,
    format_sector_etfs,
    format_style_board,
)
from bot.formatters import action_keyboard, format_signal_card, is_urgent
from bot.holidays import holiday_note, is_trading_day as ny_trading_day
from bot.market_data import market_context, scan_watchlist
from bot.notify import deliver, save_json_snapshot, send_photo, send_telegram, send_voice
from bot.ops import log_error, touch_status
from bot.reports import build_full_pack
from bot.risk import plan_trade
from bot.signals import Action
from bot.updates import build_tick_message
from bot.voice import synthesize_arabic, voice_script_from_update

NY = ZoneInfo("America/New_York")
RIYADH = ZoneInfo("Asia/Riyadh")


def is_trading_day(now_ny: datetime) -> bool:
    return ny_trading_day(now_ny)


def in_saudi_session(now_riyadh: datetime) -> bool:
    """Active board window: ~11:00 → 03:00 next day (Riyadh).

    Covers US premarket through after-hours; wraps past midnight.
    """
    t = now_riyadh.time()
    return t >= time(11, 0) or t <= time(3, 0)


def maybe_send_daily_poster(settings) -> None:
    """Send market poster once per Saudi day around session open."""
    now = datetime.now(RIYADH)
    marker = settings.data_dir / f"poster_sent_{now.strftime('%Y%m%d')}.flag"
    if marker.exists():
        return
    # Prefer around 11:00–11:10
    if not (time(11, 0) <= now.time() <= time(11, 10)):
        return
    path = settings.data_dir / "media" / f"poster_{now.strftime('%Y%m%d')}.png"
    out = make_market_poster(path)
    if out:
        send_photo(
            settings,
            out,
            caption="🖼 ملصق السوق اليومي — تعليمي فقط",
            also_channel=True,
        )
        marker.write_text("1", encoding="utf-8")


def run_tick() -> None:
    settings = load_settings()
    try:
        print(f"[tick] mode={settings.user_mode} light={settings.light_mode} thrift={settings.api_thrift}")
        note = holiday_note()
        if note:
            hol_flag = settings.data_dir / f"holiday_notified_{datetime.now(settings.local_tz).strftime('%Y%m%d')}.flag"
            if not hol_flag.exists():
                deliver(settings, "📅 تنبيه عطلة/إغلاق", note, also_channel=True)
                hol_flag.write_text("1", encoding="utf-8")
        snapshots = scan_watchlist(settings.watchlist)
        snapshots = [s for s in snapshots if s.last >= settings.min_price_usd]
        if not snapshots:
            # Silent on empty data — avoid spam; status still updated
            touch_status(ok=False, last_mode="tick", reason="no-snapshots")
            print("[tick] no snapshots — silent")
            return

        pack = build_full_pack(settings, snapshots)
        ctx = market_context()
        tone = ctx.get("tone", "غير متاح")
        change_by = {s.symbol: s.change_pct for s in snapshots}
        changed, body = build_tick_message(settings, pack["signals"], change_by, tone)
        stamp = datetime.now(settings.local_tz).strftime("%Y%m%d_%H%M%S")
        save_json_snapshot(settings, pack, f"tick_{stamp}.json")

        maybe_send_daily_poster(settings)

        if not changed:
            print("[tick] no change — silent (no Telegram)")
            touch_status(ok=True, last_mode="tick", changed=False, symbols=len(snapshots))
            return

        text = body
        top = next(
            (
                s
                for s in pack["signals"]
                if s.symbol != "MARKET"
                and s.action in (Action.CONSIDER_LONG, Action.WATCH_ENTRY)
                and getattr(s, "side", None) != "short"
            ),
            None,
        )
        markup = action_keyboard(top.symbol) if top else None
        market = next((s for s in pack["signals"] if s.action == Action.NO_TRADE_DAY), None)
        if market:
            text = f"🛑 {market.reason}\n\n" + text
        deliver(
            settings,
            "🔄 تحديث",
            text,
            reply_markup=markup,
            also_channel=bool(settings.telegram_channel_id),
        )
        for sig in pack["signals"]:
            if sig.symbol == "MARKET":
                continue
            if getattr(sig, "side", None) == "short" or sig.action == Action.CONSIDER_SHORT:
                continue
            if not is_urgent(settings, sig):
                continue
            plan = plan_trade(settings, sig)
            card = "🚨 عاجل — فرصة قوية\n" + format_signal_card(settings, sig, plan)
            send_telegram(
                settings,
                card,
                reply_markup=action_keyboard(sig.symbol),
                also_channel=bool(settings.telegram_channel_id),
            )
        if not settings.light_mode:
            script = voice_script_from_update(text, True)
            voice_path = settings.data_dir / "media" / f"tick_{stamp}.mp3"
            vp = synthesize_arabic(script, voice_path)
            if vp:
                send_voice(settings, vp, caption="ملخص صوتي للتحديث")
        touch_status(ok=True, last_mode="tick", changed=True, symbols=len(snapshots))
        # Keep GitHub Pages in sync with the same signal pack as Telegram
        try:
            from scripts.build_pages import main as build_pages_main

            build_pages_main()
        except Exception as e:
            print("[tick] build_pages skipped:", e)
    except Exception as e:
        log_error("run_tick", e)
        touch_status(ok=False, last_mode="tick", error=str(e))
        raise


def run_evening() -> None:
    settings = load_settings()
    snapshots = [s for s in scan_watchlist(settings.watchlist) if s.last >= settings.min_price_usd]
    if not snapshots:
        deliver(settings, "⚠️ فشل الفحص", "تعذّر جلب بيانات السوق.")
        sys.exit(1)
    pack = build_full_pack(settings, snapshots)
    stamp = datetime.now(RIYADH).strftime("%Y%m%d_%H%M%S")
    save_json_snapshot(settings, pack, f"sched_evening_{stamp}.json")
    deliver(
        settings,
        "🌙 الملخص المسائي (تلقائي)",
        pack["evening"],
        also_channel=bool(settings.telegram_channel_id),
    )
    # after-hours + calendars pack
    deliver(settings, "🌙 After-hours", format_after_hours(settings), also_channel=True)
    deliver(settings, "🏛 Fed", format_fed_calendar(), also_channel=True)
    script = voice_script_from_update(pack["evening"][:300], True)
    vp = synthesize_arabic(script, settings.data_dir / "media" / f"evening_{stamp}.mp3")
    if vp:
        send_voice(settings, vp, caption="ملخص مسائي صوتي")


def run_premarket() -> None:
    settings = load_settings()
    deliver(settings, "🌅 Premarket", format_premarket_hotlist(settings), also_channel=True)
    deliver(settings, "🧭 Sectors", format_sector_etfs(), also_channel=True)
    deliver(settings, "🌱🏦 Style", format_style_board(settings), also_channel=True)
    # News stays on the website only (publish_news.py → news-live.json)


def run_intel() -> None:
    """Midday intel: fed reminder (news is website-only)."""
    settings = load_settings()
    deliver(settings, "🏛 Fed", format_fed_calendar(), also_channel=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        default="tick",
        choices=[
            "tick", "auto", "morning", "intraday", "evening", "demo",
            "premarket", "intel", "afterhours",
        ],
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    now_ny = datetime.now(NY)
    now_sa = datetime.now(RIYADH)
    print(f"[scheduled] NY={now_ny.isoformat()} SA={now_sa.isoformat()} mode={args.mode}")

    mode = "tick" if args.mode in ("auto", "morning", "intraday") else args.mode

    if mode == "premarket":
        if not args.force and not is_trading_day(now_ny):
            print("[scheduled] skip — not a US trading day")
            return
        run_premarket()
        return

    if mode == "intel":
        if not args.force and not is_trading_day(now_ny):
            return
        run_intel()
        return

    if mode == "afterhours":
        if not args.force and not is_trading_day(now_ny):
            return
        settings = load_settings()
        deliver(settings, "🌙 After-hours", format_after_hours(settings), also_channel=True)
        return

    if mode == "tick":
        if not args.force and not is_trading_day(now_ny):
            print("[scheduled] skip — not a US trading day")
            return
        if not args.force and not in_saudi_session(now_sa):
            print("[scheduled] skip — outside 11:00–23:00 Saudi")
            return
        run_tick()
        return

    if mode == "evening":
        if not args.force and not is_trading_day(now_ny):
            print("[scheduled] skip — not a US trading day")
            return
        run_evening()
        return

    if mode == "demo":
        run_premarket()
        return

    raise SystemExit(f"unknown mode: {mode}")


if __name__ == "__main__":
    main()

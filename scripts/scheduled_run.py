#!/usr/bin/env python3
"""Scheduled runner — 5-minute ticks from 11:00 to 23:00 Saudi time."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.charts import make_market_poster
from bot.config import load_settings
from bot.formatters import action_keyboard, format_signal_card, is_urgent
from bot.market_data import market_context, scan_watchlist
from bot.notify import deliver, save_json_snapshot, send_photo, send_telegram, send_voice
from bot.reports import build_full_pack
from bot.risk import plan_trade
from bot.signals import Action
from bot.updates import build_tick_message
from bot.voice import synthesize_arabic, voice_script_from_update

NY = ZoneInfo("America/New_York")
RIYADH = ZoneInfo("Asia/Riyadh")

US_HOLIDAYS = {
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25), date(2027, 1, 1), date(2027, 1, 18),
    date(2027, 2, 15), date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5),
    date(2027, 9, 6), date(2027, 11, 25), date(2027, 12, 24),
}


def is_trading_day(now_ny: datetime) -> bool:
    d = now_ny.date()
    return now_ny.weekday() < 5 and d not in US_HOLIDAYS


def in_saudi_session(now_riyadh: datetime) -> bool:
    t = now_riyadh.time()
    return time(11, 0) <= t <= time(23, 0)


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
    print(f"[tick] mode={settings.user_mode} min_price={settings.min_price_usd}")
    snapshots = scan_watchlist(settings.watchlist)
    snapshots = [s for s in snapshots if s.last >= settings.min_price_usd]
    if not snapshots:
        deliver(settings, "🔄 تحديث", "تعذّر جلب بيانات السوق / لا رموز فوق فلتر السعر.")
        sys.exit(1)

    pack = build_full_pack(settings, snapshots)
    ctx = market_context()
    tone = ctx.get("tone", "غير متاح")
    change_by = {s.symbol: s.change_pct for s in snapshots}
    changed, body = build_tick_message(settings, pack["signals"], change_by, tone)
    stamp = datetime.now(RIYADH).strftime("%Y%m%d_%H%M%S")
    save_json_snapshot(settings, pack, f"tick_{stamp}.json")
    now = datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M")

    maybe_send_daily_poster(settings)

    if changed:
        text = body
        # Attach buttons for top actionable symbol if present
        top = next(
            (
                s
                for s in pack["signals"]
                if s.symbol != "MARKET"
                and s.action in (Action.CONSIDER_LONG, Action.CONSIDER_SHORT, Action.WATCH_ENTRY)
                and not (settings.is_beginner and s.action == Action.CONSIDER_SHORT)
            ),
            None,
        )
        markup = action_keyboard(top.symbol) if top else None
        # Market no-trade banner
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
        # Short voice note on changes
        script = voice_script_from_update(text, True)
        voice_path = settings.data_dir / "media" / f"tick_{stamp}.mp3"
        vp = synthesize_arabic(script, voice_path)
        if vp:
            send_voice(settings, vp, caption="ملخص صوتي للتحديث")
    else:
        deliver(settings, "🔄 تحديث", f"⏰ {now} (السعودية)\nلا يوجد شي جديد يابطل")


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
    # evening voice
    script = voice_script_from_update(pack["evening"][:300], True)
    vp = synthesize_arabic(script, settings.data_dir / "media" / f"evening_{stamp}.mp3")
    if vp:
        send_voice(settings, vp, caption="ملخص مسائي صوتي")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        default="tick",
        choices=["tick", "auto", "morning", "intraday", "evening", "demo"],
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    now_ny = datetime.now(NY)
    now_sa = datetime.now(RIYADH)
    print(f"[scheduled] NY={now_ny.isoformat()} SA={now_sa.isoformat()} mode={args.mode}")

    mode = "tick" if args.mode in ("auto", "morning", "intraday") else args.mode

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
        run_tick()
        run_tick()
        return

    raise SystemExit(f"unknown mode: {mode}")


if __name__ == "__main__":
    main()

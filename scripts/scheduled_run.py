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

from bot.config import load_settings
from bot.market_data import market_context, scan_watchlist
from bot.notify import deliver, save_json_snapshot
from bot.reports import build_full_pack
from bot.updates import build_tick_message

NY = ZoneInfo("America/New_York")
RIYADH = ZoneInfo("Asia/Riyadh")

US_HOLIDAYS = {
    date(2026, 1, 1),
    date(2026, 1, 19),
    date(2026, 2, 16),
    date(2026, 4, 3),
    date(2026, 5, 25),
    date(2026, 6, 19),
    date(2026, 7, 3),
    date(2026, 9, 7),
    date(2026, 11, 26),
    date(2026, 12, 25),
    date(2027, 1, 1),
    date(2027, 1, 18),
    date(2027, 2, 15),
    date(2027, 5, 31),
    date(2027, 6, 18),
    date(2027, 7, 5),
    date(2027, 9, 6),
    date(2027, 11, 25),
    date(2027, 12, 24),
}


def is_trading_day(now_ny: datetime) -> bool:
    d = now_ny.date()
    if now_ny.weekday() >= 5:
        return False
    if d in US_HOLIDAYS:
        return False
    return True


def in_saudi_session(now_riyadh: datetime) -> bool:
    """11:00 inclusive through 23:00 inclusive."""
    t = now_riyadh.time()
    return time(11, 0) <= t <= time(23, 0)


def run_tick() -> None:
    settings = load_settings()
    print(f"[tick] telegram={settings.telegram_enabled}")
    snapshots = scan_watchlist(settings.watchlist)
    if not snapshots:
        deliver(
            settings,
            "🔄 تحديث",
            "تعذّر جلب بيانات السوق الآن — نعيد المحاولة بعد 5 دقائق.",
        )
        sys.exit(1)

    pack = build_full_pack(settings, snapshots)
    ctx = market_context()
    tone = ctx.get("tone", "غير متاح")
    change_by = {s.symbol: s.change_pct for s in snapshots}
    changed, body = build_tick_message(
        settings,
        pack["signals"],
        change_by,
        tone,
    )
    stamp = datetime.now(RIYADH).strftime("%Y%m%d_%H%M%S")
    save_json_snapshot(settings, pack, f"tick_{stamp}.json")
    now = datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M")
    if changed:
        text = body
    else:
        text = f"⏰ {now} (السعودية)\nلا يوجد شي جديد يابطل"
    deliver(settings, "🔄 تحديث", text)


def run_evening() -> None:
    settings = load_settings()
    snapshots = scan_watchlist(settings.watchlist)
    if not snapshots:
        deliver(settings, "⚠️ فشل الفحص", "تعذّر جلب بيانات السوق.")
        sys.exit(1)
    pack = build_full_pack(settings, snapshots)
    stamp = datetime.now(RIYADH).strftime("%Y%m%d_%H%M%S")
    save_json_snapshot(settings, pack, f"sched_evening_{stamp}.json")
    deliver(settings, "🌙 الملخص المسائي (تلقائي)", pack["evening"])


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

    mode = args.mode
    if mode in ("auto", "morning", "intraday"):
        mode = "tick"

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
        run_tick()  # second pass should say لا يوجد شي جديد
        return

    raise SystemExit(f"unknown mode: {mode}")


if __name__ == "__main__":
    main()

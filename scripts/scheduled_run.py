#!/usr/bin/env python3
"""Smart scheduled runner — picks morning/intraday/evening from NY time."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.config import load_settings
from bot.market_data import scan_watchlist
from bot.notify import deliver, save_json_snapshot
from bot.reports import build_full_pack

NY = ZoneInfo("America/New_York")

# Rough US market holidays 2026–2027 (extend as needed)
US_HOLIDAYS = {
    date(2026, 1, 1),
    date(2026, 1, 19),
    date(2026, 2, 16),
    date(2026, 4, 3),
    date(2026, 5, 25),
    date(2026, 6, 19),
    date(2026, 7, 3),  # observed Independence
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


def is_trading_day(now: datetime) -> bool:
    d = now.date()
    if now.weekday() >= 5:
        return False
    if d in US_HOLIDAYS:
        return False
    return True


def resolve_mode(now: datetime, requested: str) -> str | None:
    """Return mode to run, or None to skip."""
    if requested != "auto":
        return requested

    if not is_trading_day(now):
        return None

    t = now.time()
    # Morning brief: 09:00–09:35 ET
    if time(9, 0) <= t <= time(9, 35):
        return "morning"
    # Intraday scans: 09:45–15:55 ET
    if time(9, 45) <= t <= time(15, 55):
        return "intraday"
    # Evening summary: 16:05–16:40 ET
    if time(16, 5) <= t <= time(16, 40):
        return "evening"
    return None


def run(mode: str) -> None:
    settings = load_settings()
    print(f"[scheduled] mode={mode} telegram={settings.telegram_enabled}")
    snapshots = scan_watchlist(settings.watchlist)
    if not snapshots:
        deliver(settings, "⚠️ فشل الفحص", "تعذّر جلب بيانات السوق.")
        sys.exit(1)

    pack = build_full_pack(settings, snapshots)
    stamp = datetime.now(NY).strftime("%Y%m%d_%H%M%S")
    save_json_snapshot(settings, pack, f"sched_{mode}_{stamp}.json")

    if mode == "morning":
        deliver(settings, "🌅 التقرير الصباحي (تلقائي)", pack["morning"])
    elif mode == "intraday":
        if not pack["intraday"]:
            # Quiet during empty scans — log only, avoid spam
            print("[scheduled] no strong intraday alerts")
            (settings.logs_dir / "last_intraday_quiet.txt").write_text(
                f"{stamp}: no alerts\n", encoding="utf-8"
            )
        else:
            for i, msg in enumerate(pack["intraday"], 1):
                deliver(settings, f"🚨 تنبيه تلقائي #{i}", msg)
    elif mode == "evening":
        deliver(settings, "🌙 الملخص المسائي (تلقائي)", pack["evening"])
    elif mode == "demo":
        deliver(settings, "🌅 التقرير الصباحي (تلقائي)", pack["morning"])
        for i, msg in enumerate(pack["intraday"], 1):
            deliver(settings, f"🚨 تنبيه تلقائي #{i}", msg)
        deliver(settings, "🌙 الملخص المسائي (تلقائي)", pack["evening"])
    else:
        raise SystemExit(f"unknown mode: {mode}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        default="auto",
        choices=["auto", "morning", "intraday", "evening", "demo"],
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even on weekends/holidays when mode=auto resolves",
    )
    args = parser.parse_args()
    now = datetime.now(NY)
    print(f"[scheduled] now NY={now.isoformat()}")

    if args.mode == "auto" and not args.force and not is_trading_day(now):
        print("[scheduled] skip — not a US trading day")
        return

    mode = resolve_mode(now, args.mode)
    if mode is None:
        print("[scheduled] skip — outside alert windows")
        return

    run(mode)


if __name__ == "__main__":
    main()

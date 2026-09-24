#!/usr/bin/env python3
"""US day-trade alert runner — NEVER places orders."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

# Allow running as `python main.py` from project root
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.config import load_settings
from bot.journal import add_trade, summarize_journal
from bot.market_data import scan_watchlist
from bot.notify import deliver, save_json_snapshot
from bot.reports import build_full_pack


def cmd_scan(mode: str) -> None:
    settings = load_settings()
    print(f"جاري فحص {len(settings.watchlist)} رمزًا...")
    snapshots = scan_watchlist(settings.watchlist)
    if not snapshots:
        print("تعذّر جلب البيانات. تحقق من الاتصال.")
        sys.exit(1)

    pack = build_full_pack(settings, snapshots)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = save_json_snapshot(settings, pack, f"scan_{stamp}.json")

    if mode in ("morning", "all", "demo"):
        deliver(settings, "🌅 التقرير الصباحي", pack["morning"])
    if mode in ("intraday", "all", "demo"):
        if not pack["intraday"]:
            deliver(settings, "🚨 تنبيهات الجلسة", "لا توجد تنبيهات دخول قوية الآن.")
        else:
            for i, msg in enumerate(pack["intraday"], 1):
                deliver(settings, f"🚨 تنبيه #{i}", msg)
    if mode in ("evening", "all", "demo"):
        deliver(settings, "🌙 الملخص المسائي", pack["evening"])

    print(f"\n✅ تم الحفظ: {out}")
    print("🚫 الوضع: تنبيهات فقط — لا يوجد تنفيذ أوامر.")


def cmd_journal_add(args: argparse.Namespace) -> None:
    settings = load_settings()
    row = add_trade(
        settings,
        symbol=args.symbol,
        side=args.side,
        shares=args.shares,
        entry=args.entry,
        exit_px=args.exit,
        notes=args.notes or "",
    )
    print("تم تسجيل الصفقة:")
    print(row)
    print(summarize_journal(settings))


def cmd_journal_summary() -> None:
    settings = load_settings()
    print(summarize_journal(settings))


def cmd_risk() -> None:
    settings = load_settings()
    from bot.risk import risk_banner

    print(risk_banner(settings))
    print(
        f"\nقائمة المراقبة ({len(settings.watchlist)}): "
        + ", ".join(settings.watchlist)
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="مساعد تنبيهات تداول يومي للأسهم الأمريكية (بدون تنفيذ)"
    )
    sub = p.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="فحص السوق وإصدار التقارير")
    scan.add_argument(
        "--mode",
        choices=["morning", "intraday", "evening", "all", "demo"],
        default="demo",
        help="نوع التقرير (demo = الكل للتجربة)",
    )

    risk = sub.add_parser("risk", help="عرض إعدادات رأس المال والمخاطرة")

    jadd = sub.add_parser("journal-add", help="تسجيل صفقة يدوية")
    jadd.add_argument("--symbol", required=True)
    jadd.add_argument("--side", default="long", choices=["long", "short"])
    jadd.add_argument("--shares", type=int, required=True)
    jadd.add_argument("--entry", type=float, required=True)
    jadd.add_argument("--exit", type=float, required=True)
    jadd.add_argument("--notes", default="")

    sub.add_parser("journal", help="ملخص دفتر الصفقات")
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "scan":
        cmd_scan(args.mode)
    elif args.command == "risk":
        cmd_risk()
    elif args.command == "journal-add":
        cmd_journal_add(args)
    elif args.command == "journal":
        cmd_journal_summary()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

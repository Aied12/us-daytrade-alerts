#!/usr/bin/env python3
"""Build a rich static dashboard for GitHub Pages."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.config import load_settings
from bot.holidays import holiday_note, is_trading_day
from bot.market_data import market_context, scan_watchlist
from bot.ops import read_status, touch_status
from bot.reports import build_full_pack
from bot.risk import plan_trade, risk_banner
from bot.signals import Action


def main() -> None:
    settings = load_settings()
    out_dirs = [ROOT / "pages", ROOT / "docs"]
    for d in out_dirs:
        d.mkdir(parents=True, exist_ok=True)

    ctx = market_context()
    snaps = scan_watchlist(settings.watchlist)
    snaps = [s for s in snaps if s.last >= settings.min_price_usd]
    pack = build_full_pack(settings, snaps) if snaps else {"signals": []}

    opportunities = []
    for sig in pack.get("signals") or []:
        if sig.symbol == "MARKET":
            continue
        if sig.action not in (
            Action.CONSIDER_LONG,
            Action.CONSIDER_SHORT,
            Action.WATCH_ENTRY,
            Action.AVOID,
        ):
            continue
        if settings.is_beginner and sig.action == Action.CONSIDER_SHORT:
            continue
        plan = plan_trade(settings, sig)
        opportunities.append(
            {
                "symbol": sig.symbol,
                "action": sig.action.value,
                "score_100": getattr(sig, "score_100", int(sig.score * 10)),
                "strategies": (sig.strategies or [])[:4],
                "reason": sig.reason[:180],
                "entry": plan.entry,
                "stop": plan.stop,
                "target": plan.target,
                "shares": plan.shares,
                "risk_sar": plan.risk_sar,
                "allowed": plan.allowed,
            }
        )
        if len(opportunities) >= 8:
            break

    movers = sorted(snaps, key=lambda s: abs(s.change_pct), reverse=True)[:8]
    movers_out = [
        {
            "symbol": s.symbol,
            "change_pct": round(s.change_pct, 2),
            "last": round(s.last, 2),
        }
        for s in movers
    ]

    touch_status(ok=True, source="build_pages", last_mode="dashboard")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_local": datetime.now(settings.local_tz).strftime("%Y-%m-%d %H:%M %Z"),
        "status": read_status(),
        "telegram_enabled": settings.telegram_enabled,
        "mode": settings.user_mode,
        "light_mode": settings.light_mode,
        "timezone": settings.timezone_name,
        "trading_day": is_trading_day(),
        "holiday_note": holiday_note(),
        "capital_sar": settings.capital_sar,
        "risk_banner": risk_banner(settings),
        "market": {
            "tone": ctx.get("tone"),
            "avg_change_pct": ctx.get("avg_change_pct"),
            "details": ctx.get("details") or {},
            "no_trade_today": ctx.get("no_trade_today"),
        },
        "opportunities": opportunities,
        "movers": movers_out,
        "bot": "@Aied01_bot",
        "channel": "@aied01",
        "disclaimer": "تعليمي فقط — ليس توصية استثمارية. لا يوجد تنفيذ أوامر تلقائي.",
    }

    for d in out_dirs:
        (d / "status.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("wrote", d / "status.json")


if __name__ == "__main__":
    main()

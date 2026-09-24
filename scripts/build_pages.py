#!/usr/bin/env python3
"""Build rich static dashboard JSON + keep HTML in sync for GitHub Pages."""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.config import load_settings
from bot.extras import GROWTH_NAMES, VALUE_NAMES, SECTOR_ETFS
from bot.holidays import holiday_note, is_trading_day
from bot.journal import actions_path, ensure_actions, ensure_journal, journal_path
from bot.liquidity import liquidity_dict
from bot.market_data import fetch_history, market_context, scan_watchlist, sector_momentum
from bot.ops import read_status, touch_status
from bot.reports import build_full_pack
from bot.risk import plan_trade, risk_banner
from bot.signals import Action

NY = ZoneInfo("America/New_York")


def _journal_stats(settings) -> dict:
    ensure_journal(settings)
    rows = list(csv.DictReader(journal_path(settings).open(encoding="utf-8")))
    if not rows:
        return {"trades": 0, "wins": 0, "losses": 0, "pnl_sar": 0.0, "text": "لا صفقات مسجّلة بعد"}
    total = sum(float(r["pnl_sar"]) for r in rows)
    wins = sum(1 for r in rows if float(r["pnl_sar"]) > 0)
    losses = sum(1 for r in rows if float(r["pnl_sar"]) < 0)
    return {
        "trades": len(rows),
        "wins": wins,
        "losses": losses,
        "pnl_sar": round(total, 2),
        "text": f"{len(rows)} صفقة | رابحة {wins} | خاسرة {losses} | صافي {total:,.2f} ر.س",
    }


def _recent_actions(settings, limit: int = 5) -> list[dict]:
    ensure_actions(settings)
    rows = list(csv.DictReader(actions_path(settings).open(encoding="utf-8")))
    labels = {"entered": "دخلت", "watching": "راقبت", "ignored": "تجاهلت"}
    out = []
    for r in reversed(rows[-limit:]):
        out.append(
            {
                "symbol": r.get("symbol"),
                "action": r.get("action"),
                "action_ar": labels.get(r.get("action", ""), r.get("action")),
                "at": r.get("timestamp_utc"),
            }
        )
    return out


def _alerts_today(settings) -> int:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    n = 0
    for p in settings.data_dir.glob(f"tick_{day}_*.json"):
        n += 1
    for p in settings.logs_dir.glob("cron_*.log"):
        try:
            if day in p.name:
                n += 1
        except Exception:
            pass
    # also count actions today
    ensure_actions(settings)
    for r in csv.DictReader(actions_path(settings).open(encoding="utf-8")):
        ts = r.get("timestamp_utc") or ""
        if ts.startswith(datetime.now(timezone.utc).strftime("%Y-%m-%d")):
            n += 1
    return n


def _sector_board(snaps) -> dict:
    mom = sector_momentum(snaps)
    if not mom:
        # fallback ETFs quick
        rows = []
        for etf, name in list(SECTOR_ETFS.items())[:8]:
            try:
                df = fetch_history(etf, period="5d", interval="1d")
                if df.empty or len(df) < 2:
                    continue
                chg = (float(df["Close"].iloc[-1]) / float(df["Close"].iloc[-2]) - 1) * 100
                rows.append((chg, name, etf))
            except Exception:
                continue
        rows.sort(reverse=True)
        strongest = [{"name": n, "etf": e, "change_pct": round(c, 2)} for c, n, e in rows[:3]]
        weakest = [{"name": n, "etf": e, "change_pct": round(c, 2)} for c, n, e in rows[-3:]]
        return {"strongest": strongest, "weakest": list(reversed(weakest))}
    items = sorted(mom.items(), key=lambda x: x[1], reverse=True)
    return {
        "strongest": [{"name": k, "change_pct": v} for k, v in items[:3]],
        "weakest": [{"name": k, "change_pct": v} for k, v in items[-3:][::-1]],
    }


def _style_board(snaps) -> dict:
    growth = [s for s in snaps if s.symbol in GROWTH_NAMES]
    value = [s for s in snaps if s.symbol in VALUE_NAMES]
    g = sum(s.change_pct for s in growth) / len(growth) if growth else 0.0
    v = sum(s.change_pct for s in value) / len(value) if value else 0.0
    leader = "نمو" if g > v else "قيمة" if v > g else "تعادل"
    return {
        "growth_avg": round(g, 2),
        "value_avg": round(v, 2),
        "leader": leader,
        "growth_count": len(growth),
        "value_count": len(value),
    }


def _session_countdown() -> dict:
    now = datetime.now(NY)
    open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = now.replace(hour=16, minute=0, second=0, microsecond=0)
    if now < open_t:
        phase = "before_open"
        target = open_t
        label = "للافتتاح"
    elif now < close_t:
        phase = "open"
        target = close_t
        label = "للإغلاق"
    else:
        phase = "after_close"
        target = open_t
        label = "مغلق"
    secs = int((target - now).total_seconds()) if phase != "after_close" else 0
    if phase == "after_close":
        secs = 0
    return {
        "phase": phase,
        "label_ar": label,
        "seconds_left": max(secs, 0),
        "ny_time": now.strftime("%H:%M:%S"),
        "open": "09:30",
        "close": "16:00",
    }


def main() -> None:
    settings = load_settings()
    out_dirs = [ROOT / "pages", ROOT / "docs"]
    html_src = ROOT / "pages" / "index.html"
    for d in out_dirs:
        d.mkdir(parents=True, exist_ok=True)

    ctx = market_context()
    snaps = scan_watchlist(settings.watchlist)
    snaps = [s for s in snaps if s.last >= settings.min_price_usd]
    pack = build_full_pack(settings, snaps) if snaps else {"signals": []}
    by_sym = {s.symbol: s for s in snaps}

    opportunities = []
    for sig in pack.get("signals") or []:
        if sig.symbol == "MARKET":
            continue
        if sig.action not in (
            Action.CONSIDER_LONG,
            Action.WATCH_ENTRY,
            Action.AVOID,
            Action.TAKE_PROFIT_ZONE,
        ):
            continue
        if getattr(sig, "side", None) == "short" or sig.action == Action.CONSIDER_SHORT:
            continue
        plan = plan_trade(settings, sig)
        snap = by_sym.get(sig.symbol)
        score100 = getattr(sig, "score_100", int(sig.score * 10))
        urgent = sig.action == Action.CONSIDER_LONG and score100 >= 70
        liq = liquidity_dict(snap)
        opportunities.append(
            {
                "symbol": sig.symbol,
                "action": sig.action.value,
                "action_key": sig.action.name,
                "side": sig.side,
                "score_100": score100,
                "urgent": urgent,
                "strategies": (sig.strategies or [])[:4],
                "reason": sig.reason[:220],
                "entry": plan.entry,
                "stop": plan.stop,
                "target": plan.target,
                "shares": plan.shares,
                "risk_sar": plan.risk_sar,
                "allowed": plan.allowed,
                "change_pct": round(snap.change_pct, 2) if snap else 0.0,
                "liquidity": liq,
                "rvol": liq["rvol"],
                "liq_grade": liq["grade"],
                "liq_grade_ar": liq["grade_ar"],
                "tv_url": f"https://www.tradingview.com/chart/?symbol={sig.symbol}",
                "tg_share": (
                    f"https://t.me/share/url?url=&text="
                    f"{sig.symbol}%20{sig.action.value}%0A"
                    f"دخول%20{plan.entry}%20وقف%20{plan.stop}%20هدف%20{plan.target}%0A"
                    f"سيولة%20{liq['grade_ar']}%20RVOL%20x{liq['rvol']}"
                ),
            }
        )

    movers = sorted(snaps, key=lambda s: abs(s.change_pct), reverse=True)[:8]
    indexes = []
    for k, v in (ctx.get("details") or {}).items():
        indexes.append({"symbol": k, "change_pct": v.get("change_pct", 0), "rsi": v.get("rsi")})

    touch_status(ok=True, source="build_pages", last_mode="dashboard")
    generated_at = datetime.now(timezone.utc)
    payload = {
        "generated_at": generated_at.isoformat(),
        "generated_ts": int(generated_at.timestamp()),
        "generated_local": datetime.now(settings.local_tz).strftime("%Y-%m-%d %H:%M %Z"),
        "status": read_status(),
        "telegram_enabled": settings.telegram_enabled,
        "mode": settings.user_mode,
        "is_beginner": settings.is_beginner,
        "light_mode": settings.light_mode,
        "timezone": settings.timezone_name,
        "trading_day": is_trading_day(),
        "holiday_note": holiday_note(),
        "capital_sar": settings.capital_sar,
        "daily_loss_limit_sar": settings.daily_loss_budget_sar,
        "risk_banner": risk_banner(settings),
        "market": {
            "tone": ctx.get("tone"),
            "avg_change_pct": ctx.get("avg_change_pct"),
            "details": ctx.get("details") or {},
            "indexes": indexes,
            "no_trade_today": ctx.get("no_trade_today"),
            "price_provider": ctx.get("price_provider") or "yahoo",
            "price_lag_ar": ctx.get("price_lag_ar") or "تأخير Yahoo ≈ 15 دقيقة",
        },
        "price_provider": ctx.get("price_provider") or "yahoo",
        "price_lag_ar": ctx.get("price_lag_ar") or "تأخير Yahoo ≈ 15 دقيقة",
        "has_live_quotes": settings.has_live_quotes,
        "session": _session_countdown(),
        "sectors": _sector_board(snaps),
        "style": _style_board(snaps),
        "opportunities": opportunities,
        "movers": [
            {
                "symbol": s.symbol,
                "change_pct": round(s.change_pct, 2),
                "last": round(s.last, 2),
                **{k: liquidity_dict(s)[k] for k in ("rvol", "grade", "grade_ar", "dollar_volume_label")},
            }
            for s in movers
        ],
        "performance": {
            "journal": _journal_stats(settings),
            "alerts_today": _alerts_today(settings),
            "recent_actions": _recent_actions(settings, 5),
            "stop_discipline_pct": None,  # 19 later
        },
        "bot": "@Aied01_bot",
        "channel": "@aied01",
        "disclaimer": "تعليمي فقط — ليس توصية استثمارية. لا يوجد تنفيذ أوامر تلقائي.",
        "reminder": "لا تدخل إذا وصلت حد الخسارة اليومي",
    }

    for d in out_dirs:
        (d / "status.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if html_src.exists() and d != ROOT / "pages":
            (d / "index.html").write_text(html_src.read_text(encoding="utf-8"), encoding="utf-8")
        print("wrote", d / "status.json")


if __name__ == "__main__":
    main()

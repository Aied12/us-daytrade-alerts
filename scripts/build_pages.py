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
from bot.flow_filter import gainer_passes_flow, momentum_rank_score, passes_daytrade_flow
from bot.gainers import fetch_day_gainers, watchlist_gainers
from bot.live_quotes import price_lag_label_ar, session_phase
from bot.liquidity import liquidity_dict
from bot.market_data import fetch_history, market_context, scan_watchlist, sector_momentum
from bot.news_ar import fetch_stock_news_ar
from bot.ops import read_status, touch_status
from bot.reports import build_full_pack
from bot.risk import plan_trade, risk_banner
from bot.jamal_strategy import (
    JamalSettings,
    build_jamal_scanner,
    jamal_to_opportunity,
)
from bot.signals import Action
from bot.smart_signals import enrich_smart_signal
from bot.catalyst_scan import build_momentum_scanner, enrich_news_item, rank_catalyst_news
from bot.sniper_scan import build_sniper_scanner, fetch_cheap_runners

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
    phase = session_phase()
    min_px = max(float(settings.min_price_usd), 5.0)

    # Refresh candidate universe: watchlist + day gainers + most-actives (via gainers feed)
    gainers = fetch_day_gainers(min_price=min_px, limit=25)
    gainer_syms = [g["symbol"] for g in (gainers or []) if g.get("symbol")]
    scan_syms = list(dict.fromkeys([*(settings.watchlist or []), *gainer_syms[:18]]))
    snaps = scan_watchlist(scan_syms)
    snaps = [s for s in snaps if s.last >= settings.min_price_usd]
    if not gainers:
        gainers = watchlist_gainers(snaps, min_price=min_px, limit=15)
    # Drop quiet gainers early
    gainers = [g for g in (gainers or []) if gainer_passes_flow(g, phase)]

    pack = build_full_pack(settings, snaps) if snaps else {"signals": []}
    by_sym = {s.symbol: s for s in snaps}
    qqq_chg = float(((ctx.get("details") or {}).get("QQQ") or {}).get("change_pct") or 0.0)

    opportunities = []
    rejected_slow: list[str] = []
    for sig in pack.get("signals") or []:
        if sig.symbol == "MARKET":
            continue
        # أفضل الفرص = شراء / مراقبة فقط (لا تجنّب ولا بيع قصير)
        if sig.action not in (Action.CONSIDER_LONG, Action.WATCH_ENTRY):
            continue
        if getattr(sig, "side", None) == "short" or sig.action == Action.CONSIDER_SHORT:
            continue
        snap = by_sym.get(sig.symbol)
        live_last = round(snap.last, 2) if snap else round(float(sig.entry_hint or 0), 2)
        if live_last <= 0:
            continue
        # Premarket: drop names already red — setup expired
        if phase == "pre" and snap and snap.change_pct < -0.35:
            continue
        ok_flow, flow_reason = passes_daytrade_flow(snap, phase)
        if not ok_flow:
            rejected_slow.append(f"{sig.symbol}:{flow_reason}")
            continue
        smart = enrich_smart_signal(sig, snap, qqq_chg=qqq_chg, live_last=live_last)
        # 29/30/23 — drop earnings-imminent, expired chase, or severe fake-liquidity junk
        if smart.get("exclude") or smart.get("expired"):
            rejected_slow.append(f"{sig.symbol}:{smart.get('exclude_reason') or smart.get('expired_ar') or 'منتهية'}")
            continue
        plan = plan_trade(settings, sig, live_last=live_last)
        entry_original = float(sig.entry_hint or live_last)
        # Hard rule: long entry must never exceed live price
        if (sig.side or "long") == "long" and plan.entry > live_last:
            plan.entry = live_last
        score100 = getattr(sig, "score_100", int(sig.score * 10))
        urgent = (
            sig.action == Action.CONSIDER_LONG
            and score100 >= 70
            and smart.get("confidence") in ("A", "B")
            and not smart.get("expired")
        )
        if phase == "pre" and snap and snap.change_pct < 0:
            urgent = False
        if smart.get("wait_1m_confirm"):
            urgent = False
        liq = liquidity_dict(snap)
        flow_score = momentum_rank_score(snap)
        # Prefer alt stop on card when available
        stop_show = smart.get("stop_alt") or plan.stop
        target_partial = smart.get("target_partial")
        target_final = smart.get("target_final") or plan.target
        opportunities.append(
            {
                "symbol": sig.symbol,
                "action": sig.action.value,
                "action_key": sig.action.name,
                "side": sig.side,
                "score_100": score100,
                "confidence": smart.get("confidence"),
                "confidence_ar": smart.get("confidence_ar"),
                "urgent": urgent,
                "strategies": (sig.strategies or [])[:4],
                "reason": sig.reason[:220],
                "entry": plan.entry,
                "entry_original": round(entry_original, 2),
                "entry_planned": round(float(sig.entry_hint or plan.entry), 2),
                "stop": plan.stop,
                "stop_alt": smart.get("stop_alt"),
                "target": plan.target,
                "target_partial": target_partial,
                "target_final": target_final,
                "trail_offset": smart.get("trail_offset"),
                "shares": plan.shares,
                "risk_sar": plan.risk_sar,
                "allowed": plan.allowed
                and not (phase == "pre" and snap and snap.change_pct < -0.5)
                and not smart.get("wait_1m_confirm")
                and not smart.get("expired")
                and smart.get("confidence") != "C",
                "change_pct": round(snap.change_pct, 2) if snap else 0.0,
                "last": live_last,
                "session_phase": phase,
                "liquidity": liq,
                "rvol": liq["rvol"],
                "rvol_pace": liq.get("rvol_pace"),
                "liq_grade": liq["grade"],
                "liq_grade_ar": liq["grade_ar"],
                "atr_pct": round(float(snap.atr_pct or 0), 2) if snap else 0.0,
                "flow_score": round(flow_score, 2),
                "flow_ok": True,
                "smart": smart,
                "smart_tags": smart.get("tags") or [],
                "smart_notes": smart.get("notes") or [],
                "wait_1m_confirm": bool(smart.get("wait_1m_confirm")),
                "fake_liquidity": bool(smart.get("fake_liquidity")),
                "expired": bool(smart.get("expired")),
                "candle": smart.get("candle") or {},
                "vwap": smart.get("vwap") or {},
                "qqq": smart.get("qqq") or {},
                "tv_url": f"https://www.tradingview.com/chart/?symbol={sig.symbol}",
                "tg_share": (
                    f"https://t.me/share/url?url=&text="
                    f"{sig.symbol}%20{sig.action.value}%20ثقة%20{smart.get('confidence')}%0A"
                    f"الآن%20{live_last}%20دخول%20{plan.entry}%20وقف%20{stop_show}%20هدف%20{target_final}%0A"
                    f"سيولة%20{liq['grade_ar']}%20وتيرة%20x{liq.get('rvol_pace')}%20زخم%20{snap.change_pct:+.2f}%"
                ),
            }
        )

    # Rank: confidence then flow
    rank_conf = {"A": 3, "B": 2, "C": 1}
    opportunities.sort(
        key=lambda o: (
            rank_conf.get(o.get("confidence") or "C", 0),
            float(o.get("flow_score") or 0),
            1 if o.get("urgent") else 0,
            o.get("score_100") or 0,
            o.get("change_pct") or 0,
        ),
        reverse=True,
    )
    opportunities = opportunities[:12]
    # Belt-and-suspenders: never publish expired / chase cards to خطط الدخول
    opportunities = [
        o
        for o in opportunities
        if not o.get("expired")
        and not (o.get("smart") or {}).get("expired")
        and not any(
            (isinstance(t, dict) and (t.get("key") == "expired" or "انتهت" in str(t.get("ar") or "")))
            for t in (o.get("smart_tags") or [])
        )
    ]

    movers = sorted(snaps, key=lambda s: abs(s.change_pct), reverse=True)[:8]

    # News symbols: opportunities + gainers + core watchlist (unique, capped)
    news_syms: list[str] = []
    for o in opportunities:
        if o.get("symbol"):
            news_syms.append(str(o["symbol"]).upper())
    for g in gainers or []:
        if g.get("symbol"):
            news_syms.append(str(g["symbol"]).upper())
    for s in settings.watchlist or []:
        news_syms.append(str(s).upper())
    news_syms = list(dict.fromkeys(news_syms))[:22]
    try:
        news_pack = fetch_stock_news_ar(
            news_syms,
            limit=24,
            per_symbol=4,
            drop_negative_symbols=True,
            translate_summary=False,
            include_market=True,
        )
    except Exception:
        news_pack = {"news": [], "negative_symbols": []}
    news_ar = list(news_pack.get("news") or [])
    bad_news = {str(s).upper() for s in (news_pack.get("negative_symbols") or [])}

    # Drop any stock with negative headlines from boards + news (already filtered)
    if bad_news:
        opportunities = [o for o in opportunities if str(o.get("symbol") or "").upper() not in bad_news]
        gainers = [g for g in (gainers or []) if str(g.get("symbol") or "").upper() not in bad_news]
        movers = [s for s in movers if s.symbol.upper() not in bad_news]
        news_ar = [n for n in news_ar if str(n.get("symbol") or "").upper() not in bad_news]

    # StockTitan-style: enrich + rank catalyst feed, then Argus-like momentum scanner
    chg_by = {s.symbol.upper(): float(s.change_pct) for s in snaps}
    for g in gainers or []:
        sym = str(g.get("symbol") or "").upper()
        if sym and sym not in chg_by:
            chg_by[sym] = float(g.get("change_pct") or 0)
    news_ar = [
        enrich_news_item(n, change_pct=chg_by.get(str(n.get("symbol") or "").upper()))
        for n in news_ar
    ]
    news_ar = rank_catalyst_news(news_ar, limit=30)
    momentum = build_momentum_scanner(
        snaps=snaps,
        gainers=gainers,
        news=news_ar,
        phase=phase,
        limit=12,
    )

    # Sniper: Hessa-style cents auto board (≤$1 / ≤$2) + plan — no manual picks
    cheap_runners = []
    try:
        cheap_runners = fetch_cheap_runners(limit=30)
    except Exception:
        cheap_runners = []
    sniper = build_sniper_scanner(
        runners=cheap_runners,
        news=news_ar,
        phase=phase,
        limit=12,
    )

    # استراتيجية جمال — Setup ثم Entry Trigger (لا دخول على المؤشرات وحدها)
    jamal_cfg = JamalSettings()
    try:
        jamal_cards = build_jamal_scanner(snaps, settings=settings, jamal=jamal_cfg, limit=10)
    except Exception:
        jamal_cards = []
    jamal_opps = [jamal_to_opportunity(c) for c in jamal_cards if c]
    try:
        bad = set(bad_news) if bad_news else set()
    except Exception:
        bad = set()
    jamal_opps = [jo for jo in jamal_opps if str(jo.get("symbol") or "").upper() not in bad]
    # ادمج في خطط الدخول مع وسم جمال (بدون تكرار الرمز إن وُجد)
    seen_opp = {str(o.get("symbol") or "").upper() for o in opportunities}
    for jo in jamal_opps:
        sym = str(jo.get("symbol") or "").upper()
        if not sym:
            continue
        if sym in seen_opp:
            # علّم البطاقة الحالية بوسم جمال إن وُجدت
            for o in opportunities:
                if str(o.get("symbol") or "").upper() == sym:
                    tags = list(o.get("smart_tags") or [])
                    tags.insert(0, {"key": "jamal", "ar": "🎯 استراتيجية جمال"})
                    o["smart_tags"] = tags
                    o["jamal"] = jo.get("jamal") or jo
                    o["tag_ar"] = "🎯 استراتيجية جمال"
                    break
        else:
            opportunities.append(jo)
            seen_opp.add(sym)
    opportunities = opportunities[:16]

    from bot.gainers import session_label_ar

    gainers_session = session_label_ar(phase)
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
        "usd_sar_rate": settings.usd_sar_rate,
        "capital_usd": round(float(settings.capital_sar) / float(settings.usd_sar_rate or 3.75), 2),
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
        "momentum_scanner": momentum,
        "momentum_note_ar": "ماسح زخم بأسلوب Argus: تحرك ≈4%+ مع سيولة، والخبر/المحفز بجانب الحركة",
        "sniper_scanner": sniper,
        "sniper_note_ar": "ماسح القنص مستقل عن ≥$51 و$5–$51 — تحت $5 فقط · الأسهم الظاهرة تثبت ~30 د حتى لا تختفي وترجع فجأة",
        "sniper_price_band": {"min": 0.10, "max": 4.999, "cents_max": 1.0, "board_excluded_from": 5.0},
        "sniper_auto": True,
        "sniper_ignores_price_tier": True,
        "sniper_sticky_min": 30,
        "jamal_scanner": jamal_cards,
        "jamal_note_ar": "استراتيجية جمال: Setup → مراقبة → Entry Trigger (اختراق قمة+Buffer) → Stop/TP — بدون مطاردة · ليست توصية استثمارية",
        "jamal_settings": {
            "buffer_pct": jamal_cfg.buffer_pct,
            "max_chase_pct": jamal_cfg.max_chase_pct,
            "stop_loss_pct": jamal_cfg.stop_loss_pct,
            "tp1_rr": jamal_cfg.tp1_rr,
            "tp2_rr": jamal_cfg.tp2_rr,
            "trailing_enabled": jamal_cfg.trailing_enabled,
            "trailing_pct": jamal_cfg.trailing_pct,
            "signal_expiry_min": jamal_cfg.signal_expiry_min,
            "exit_before_close_min": jamal_cfg.exit_before_close_min,
            "vol_ratio_min": jamal_cfg.vol_ratio_min,
            "mfi_min": jamal_cfg.mfi_min,
        },
        "opportunities": opportunities,
        "opps_note_ar": "خطط دخول ذكية بعد الماسح — ثقة A/B/C · وقف/هدف · يشمل 🎯 استراتيجية جمال عند التوافق",
        "opps_rejected_slow": rejected_slow[:20],
        "gainers": gainers,
        "gainers_min_price": min_px,
        "gainers_session_ar": gainers_session,
        "movers": [
            {
                "symbol": s.symbol,
                "change_pct": round(s.change_pct, 2),
                "last": round(s.last, 2),
                **{k: liquidity_dict(s)[k] for k in ("rvol", "grade", "grade_ar", "dollar_volume_label")},
            }
            for s in movers
        ],
        "news": news_ar,
        "news_excluded_negative": sorted(bad_news),
        "news_note_ar": "بث محفزات: أخبار الشركات + تأثير 1–5 + وسم (FDA/أرباح/شراكة…) — بدون إشاعات",
        "scan_mode_ar": "مسح StockTitan-style: محفز رسمي ← تأثير ← زخم سعري مربوط بالخبر",
        "bot": "@Aied01_bot",
        "channel": "@aied01",
        "disclaimer": "تعليمي فقط — ليس توصية استثمارية. لا يوجد تنفيذ أوامر تلقائي.",
        "reminder": "تداول المحفز لا المطاردة — راجع المصدر قبل الدخول",
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

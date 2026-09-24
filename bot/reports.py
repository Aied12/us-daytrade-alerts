from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from bot.config import Settings
from bot.market_data import QuoteSnapshot, market_context
from bot.risk import PositionPlan, plan_trade, risk_banner
from bot.signals import Action, Signal, rank_signals

NY = ZoneInfo("America/New_York")


def _now_ny() -> str:
    return datetime.now(NY).strftime("%Y-%m-%d %H:%M %Z")


def _disclaimer() -> str:
    return (
        "\n———\n"
        "⚠️ تنبيه: هذا مساعد إشارات تعليمي فقط، وليس توصية استثمارية. "
        "لا ينفّذ أوامر. أنت المسؤول عن قراراتك وخسائرك."
    )


def format_morning(
    settings: Settings,
    snapshots: list[QuoteSnapshot],
    signals: list[Signal],
) -> str:
    ctx = market_context()
    lines = [
        "🌅 تقرير ما قبل / افتتاح الجلسة الأمريكية",
        f"⏰ {_now_ny()}",
        "",
        risk_banner(settings),
        "",
        f"📊 مزاج السوق: {ctx['tone']}",
    ]
    for sym, d in ctx.get("details", {}).items():
        lines.append(f"  • {sym}: {d['change_pct']:+.2f}% | RSI {d['rsi']}")

    actionable = [
        s
        for s in signals
        if s.action in (Action.CONSIDER_LONG, Action.WATCH_ENTRY)
    ][: settings.max_morning_picks]

    lines.append("")
    lines.append(f"🎯 أفضل الفرص اليوم ({len(actionable)}):")
    if not actionable:
        lines.append("  لا توجد فرص قوية الآن — الأفضل الانتظار.")
    else:
        for i, sig in enumerate(actionable, 1):
            plan = plan_trade(settings, sig)
            snap = next((x for x in snapshots if x.symbol == sig.symbol), None)
            chg = f"{snap.change_pct:+.1f}%" if snap else ""
            lines.append(
                f"\n{i}) {sig.symbol} — {sig.action.value} {chg}\n"
                f"   السبب: {sig.reason}\n"
                f"   دخول≈ ${plan.entry} | وقف≈ ${plan.stop} | هدف≈ ${plan.target}\n"
                f"   الحجم المقترح: {plan.shares} سهم "
                f"(≈ {plan.position_sar:,.0f} ر.س) | R:R {plan.reward_risk}\n"
                f"   ماذا تفعل: {'يمكنك التخطيط للدخول' if plan.allowed else 'راقب فقط / لا تدخل'}\n"
                f"   {plan.note}"
            )

    lines.append(_disclaimer())
    return "\n".join(lines)


def format_intraday_alert(
    settings: Settings,
    signal: Signal,
    plan: PositionPlan,
) -> str:
    lines = [
        "🚨 تنبيه أثناء الجلسة",
        f"⏰ {_now_ny()}",
        f"📌 {signal.symbol} — {signal.action.value}",
        f"السبب: {signal.reason}",
        "",
        f"المستويات: دخول≈ ${plan.entry} | وقف≈ ${plan.stop} | هدف≈ ${plan.target}",
        f"الحجم: {plan.shares} سهم | مخاطرة≈ {plan.risk_sar:,.0f} ر.س | R:R {plan.reward_risk}",
        f"ماذا تفعل الآن: {'فكّر في دخول يدوي بالحجم أعلاه' if plan.allowed else 'لا تدخل — راقب فقط'}",
        plan.note,
        "",
        f"تذكير حد اليوم: {settings.daily_loss_budget_sar:,.0f} ر.س",
        _disclaimer(),
    ]
    return "\n".join(lines)


def format_evening(
    settings: Settings,
    signals: list[Signal],
    sent_alerts: list[str],
) -> str:
    ctx = market_context()
    top = [s for s in signals if s.action != Action.WAIT][:8]
    lines = [
        "🌙 ملخص بعد الإغلاق",
        f"⏰ {_now_ny()}",
        "",
        risk_banner(settings),
        "",
        f"مزاج السوق عند الإغلاق: {ctx['tone']}",
        f"تنبيهات أُرسلت اليوم: {len(sent_alerts)}",
    ]
    if sent_alerts:
        lines.append("  • " + " | ".join(sent_alerts))

    lines.append("")
    lines.append("أبرز الإشارات:")
    if not top:
        lines.append("  لا توجد إشارات بارزة.")
    else:
        for s in top:
            lines.append(f"  • {s.symbol}: {s.action.value} (score {s.score}) — {s.reason}")

    lines.append("")
    lines.append("📋 واجب الليلة:")
    lines.append("  1) سجّل الصفقات اللي دخلتها (ربح/خسارة بالريال)")
    lines.append("  2) إذا لمست حد الخسارة اليومي — بكرة راحة أو نصف حجم")
    lines.append("  3) لا ترفع المخاطرة لتعويض الخسارة")
    lines.append(_disclaimer())
    return "\n".join(lines)


def build_full_pack(settings: Settings, snapshots: list[QuoteSnapshot]) -> dict:
    signals = rank_signals(snapshots)
    morning = format_morning(settings, snapshots, signals)
    plans = []
    intraday_messages = []
    alert_count = 0
    sent_symbols: list[str] = []

    for sig in signals:
        if sig.action not in (Action.CONSIDER_LONG, Action.WATCH_ENTRY):
            continue
        if alert_count >= settings.max_alerts_per_day:
            break
        plan = plan_trade(settings, sig)
        plans.append(plan)
        if plan.allowed or sig.action == Action.WATCH_ENTRY:
            msg = format_intraday_alert(settings, sig, plan)
            intraday_messages.append(msg)
            sent_symbols.append(sig.symbol)
            alert_count += 1

    evening = format_evening(settings, signals, sent_symbols)
    return {
        "signals": signals,
        "plans": plans,
        "morning": morning,
        "intraday": intraday_messages,
        "evening": evening,
        "sent_symbols": sent_symbols,
    }

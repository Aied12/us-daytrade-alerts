from __future__ import annotations

from dataclasses import dataclass

from bot.config import Settings
from bot.signals import Action, Signal


@dataclass
class PositionPlan:
    symbol: str
    action_ar: str
    shares: int
    entry: float
    stop: float
    target: float
    risk_usd: float
    risk_sar: float
    position_usd: float
    position_sar: float
    reward_risk: float
    allowed: bool
    note: str


def plan_trade(settings: Settings, signal: Signal) -> PositionPlan:
    entry = signal.entry_hint
    stop = signal.stop_hint
    target = signal.target_hint

    if signal.side == "short":
        risk_per_share = max(stop - entry, 0.01)
        reward = max(entry - target, 0)
    else:
        risk_per_share = max(entry - stop, 0.01)
        reward = max(target - entry, 0)

    max_risk_usd = settings.risk_budget_usd
    shares = int(max_risk_usd // risk_per_share) if risk_per_share else 0
    max_position_usd = settings.capital_usd * 0.25
    if entry > 0 and shares * entry > max_position_usd:
        shares = int(max_position_usd // entry)

    # Shorts disabled entirely — longs / watch only
    if signal.side == "short" or signal.action == Action.CONSIDER_SHORT:
        allowed = False
        note = "البيع القصير موقوف — لا يُعرض ولا يُنفَّذ"
    elif signal.action == Action.NO_TRADE_DAY:
        allowed = False
        note = "إشارة يوم: لا تتداول اليوم"
    else:
        allowed = shares >= 1 and signal.side == "long" and signal.action in (
            Action.CONSIDER_LONG,
            Action.WATCH_ENTRY,
        )
        # Only auto-allow CONSIDER_* for execution planning; WATCH is observe
        if signal.action == Action.WATCH_ENTRY:
            allowed = False
            note = "راقب فقط — لا دخول حتى تتأكد"
        elif not allowed:
            note = "لا تدخل — الإشارة ليست جاهزة أو الحجم أقل من سهم"
        else:
            rr = reward / risk_per_share if risk_per_share else 0
            if rr < 1.2:
                allowed = False
                note = "العائد/المخاطرة ضعيف — انتظر دخول أفضل"
            else:
                note = (
                    f"خاطِر بحد أقصى ~{settings.risk_budget_sar:.0f} ر.س "
                    f"({settings.risk_per_trade*100:.0f}% من رأس المال)"
                )

    position_usd = shares * entry
    risk_usd = shares * risk_per_share
    rr = reward / risk_per_share if risk_per_share else 0

    return PositionPlan(
        symbol=signal.symbol,
        action_ar=signal.action.value,
        shares=shares,
        entry=entry,
        stop=stop,
        target=target,
        risk_usd=round(risk_usd, 2),
        risk_sar=round(risk_usd * settings.usd_sar_rate, 2),
        position_usd=round(position_usd, 2),
        position_sar=round(position_usd * settings.usd_sar_rate, 2),
        reward_risk=round(rr, 2),
        allowed=allowed,
        note=note,
    )


def risk_banner(settings: Settings) -> str:
    return (
        f"💰 رأس المال: {settings.capital_sar:,.0f} ر.س "
        f"(≈ ${settings.capital_usd:,.0f})\n"
        f"⚠️ مخاطرة الصفقة: {settings.risk_budget_sar:,.0f} ر.س "
        f"({settings.risk_per_trade*100:.0f}%)\n"
        f"🛑 حد الخسارة اليومي: {settings.daily_loss_budget_sar:,.0f} ر.س "
        f"({settings.daily_loss_limit*100:.0f}%) — إذا وصلته قف فورًا\n"
        f"📌 أقصى تنبيهات دخول اليوم: {settings.max_alerts_per_day}\n"
        f"🎛 الوضع: {'مبتدئ' if settings.is_beginner else 'محترف'}"
    )

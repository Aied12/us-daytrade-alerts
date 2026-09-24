from __future__ import annotations

from bot.config import Settings
from bot.risk import PositionPlan, plan_trade
from bot.signals import Action, Signal


def strength_emoji(score_100: int | float) -> str:
    # accept legacy 0-10 or 0-100
    s = float(score_100)
    if s <= 10:
        s *= 10
    if s >= 80:
        return "🔥🔥🔥"
    if s >= 68:
        return "🔥🔥"
    if s >= 55:
        return "🔥"
    if s >= 40:
        return "👀"
    return "❄️"


def action_emoji(action: Action) -> str:
    return {
        Action.CONSIDER_LONG: "🟢",
        Action.CONSIDER_SHORT: "🔻",
        Action.WATCH_ENTRY: "🟡",
        Action.TAKE_PROFIT_ZONE: "🟣",
        Action.AVOID: "🔴",
        Action.WAIT: "⚪",
        Action.NO_TRADE_DAY: "🛑",
    }.get(action, "⚪")


def is_urgent(settings: Settings, signal: Signal) -> bool:
    return signal.action == Action.CONSIDER_LONG and signal.score_100 >= max(
        70, int(settings.urgent_min_score * 10)
    )


def action_keyboard(symbol: str) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ دخلت", "callback_data": f"act:entered:{symbol}"},
                {"text": "👀 راقبت", "callback_data": f"act:watching:{symbol}"},
                {"text": "⏭️ تجاهلت", "callback_data": f"act:ignored:{symbol}"},
            ]
        ]
    }


def mode_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "🟢 مبتدئ", "callback_data": "mode:beginner"},
                {"text": "🔵 محترف", "callback_data": "mode:pro"},
            ]
        ]
    }


def format_signal_card(
    settings: Settings,
    signal: Signal,
    plan: PositionPlan | None = None,
    *,
    for_channel: bool = False,
) -> str:
    if signal.symbol == "MARKET":
        return f"🛑 {signal.action.value}\n{signal.reason}"

    plan = plan or plan_trade(settings, signal)
    em = strength_emoji(signal.score_100)
    ae = action_emoji(signal.action)
    urgent = "🚨 عاجل — فرصة قوية\n" if is_urgent(settings, signal) else ""
    strats = "، ".join(signal.strategies[:4]) if signal.strategies else "—"

    if settings.is_beginner and not for_channel:
        lines = [
            f"{urgent}{ae} {em} {signal.symbol} | قوة {signal.score_100}/100",
            f"الإشارة: {signal.action.value}",
            f"الاستراتيجيات: {strats}",
            f"ببساطة: {'تقدر تخطط لدخول صغير' if plan.allowed else 'الآن راقب فقط — لا تستعجل'}",
            f"دخول حوالي ${plan.entry}",
            f"وقف خسارة حوالي ${plan.stop}",
            f"هدف حوالي ${plan.target}",
            f"عدد الأسهم المقترح: {plan.shares}",
            f"مخاطرة تقريبية: {plan.risk_sar:,.0f} ر.س",
            plan.note,
        ]
    else:
        lines = [
            f"{urgent}{ae} {em} {signal.symbol} | {signal.score_100}/100",
            f"{signal.action.value}",
            f"استراتيجيات: {strats}",
            f"تفاصيل: {signal.reason}",
            f"Entry ${plan.entry} | Stop ${plan.stop} | Target ${plan.target}",
            f"Shares {plan.shares} | Risk {plan.risk_sar:,.0f} SAR | R:R {plan.reward_risk}",
            plan.note,
        ]
        if for_channel:
            lines = [
                f"{urgent}{ae} {em} {signal.symbol} | {signal.score_100}/100",
                f"{signal.action.value}",
                f"استراتيجيات: {strats}",
                f"مستويات≈ ${plan.entry} / ${plan.stop} / ${plan.target}",
                "تعليمي فقط — ليس توصية استثمارية",
            ]
    return "\n".join(lines)


def public_channel_text(private_text: str) -> str:
    skip = ("رأس المال", "مخاطرة الصفقة", "حد الخسارة اليومي", "ر.س")
    lines = []
    for line in private_text.splitlines():
        if any(s in line for s in skip) and "مستويات" not in line and "مخاطرة تقريبية" not in line:
            continue
        lines.append(line)
    if "تعليمي" not in private_text:
        lines.append("تعليمي فقط — ليس توصية استثمارية")
    return "\n".join(lines)

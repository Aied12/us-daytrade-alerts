from __future__ import annotations

from bot.config import Settings
from bot.risk import PositionPlan, plan_trade
from bot.signals import Action, Signal


def strength_emoji(score: float) -> str:
    if score >= 6.5:
        return "🔥🔥🔥"
    if score >= 5.0:
        return "🔥🔥"
    if score >= 3.5:
        return "🔥"
    if score >= 2.0:
        return "👀"
    return "❄️"


def action_emoji(action: Action) -> str:
    return {
        Action.CONSIDER_LONG: "🟢",
        Action.WATCH_ENTRY: "🟡",
        Action.TAKE_PROFIT_ZONE: "🟣",
        Action.AVOID: "🔴",
        Action.WAIT: "⚪",
    }.get(action, "⚪")


def is_urgent(settings: Settings, signal: Signal) -> bool:
    return (
        signal.action == Action.CONSIDER_LONG
        and signal.score >= settings.urgent_min_score
    )


def action_keyboard(symbol: str) -> dict:
    """Inline keyboard: دخلت / راقبت / تجاهلت"""
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
    plan = plan or plan_trade(settings, signal)
    em = strength_emoji(signal.score)
    ae = action_emoji(signal.action)
    urgent = "🚨 عاجل — فرصة قوية\n" if is_urgent(settings, signal) else ""

    if settings.is_beginner and not for_channel:
        lines = [
            f"{urgent}{ae} {em} {signal.symbol}",
            f"الإشارة: {signal.action.value}",
            f"ببساطة: {'تقدر تخطط لدخول صغير' if plan.allowed else 'الآن راقب فقط — لا تستعجل'}",
            f"دخول حوالي ${plan.entry}",
            f"وقف خسارة حوالي ${plan.stop}",
            f"هدف حوالي ${plan.target}",
            f"عدد الأسهم المقترح: {plan.shares}",
            f"مخاطرة تقريبية: {plan.risk_sar:,.0f} ر.س",
        ]
    else:
        lines = [
            f"{urgent}{ae} {em} {signal.symbol} | score {signal.score}",
            f"{signal.action.value}",
            f"سبب: {signal.reason}",
            f"Entry ${plan.entry} | Stop ${plan.stop} | Target ${plan.target}",
            f"Shares {plan.shares} | Risk {plan.risk_sar:,.0f} SAR | R:R {plan.reward_risk}",
        ]
        if for_channel:
            # Public: no personal capital sizing details beyond generic
            lines = [
                f"{urgent}{ae} {em} {signal.symbol}",
                f"{signal.action.value}",
                f"{signal.reason}",
                f"مستويات تقريبية: ${plan.entry} / ${plan.stop} / ${plan.target}",
                "تعليمي فقط — ليس توصية استثمارية",
            ]
    return "\n".join(lines)


def public_channel_text(private_text: str) -> str:
    """Strip overly personal lines for public channel."""
    skip = ("رأس المال", "مخاطرة الصفقة", "حد الخسارة اليومي", "ر.س")
    lines = []
    for line in private_text.splitlines():
        if any(s in line for s in skip) and "مستويات" not in line:
            continue
        lines.append(line)
    if "تعليمي" not in private_text:
        lines.append("تعليمي فقط — ليس توصية استثمارية")
    return "\n".join(lines)

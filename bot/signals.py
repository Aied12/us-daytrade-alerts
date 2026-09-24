from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from bot.market_data import QuoteSnapshot


class Action(str, Enum):
    WATCH_ENTRY = "راقب دخول"
    CONSIDER_LONG = "فكّر في شراء قصير المدى"
    AVOID = "تجنّب الآن"
    TAKE_PROFIT_ZONE = "منطقة جني أرباح / لا تطارد"
    WAIT = "انتظر تأكيد"


@dataclass
class Signal:
    symbol: str
    action: Action
    score: float
    reason: str
    entry_hint: float
    stop_hint: float
    target_hint: float
    side: str  # long only for v1


def _vol_ratio(s: QuoteSnapshot) -> float:
    if s.avg_volume_20 <= 0:
        return 1.0
    return s.volume / s.avg_volume_20


def score_momentum_breakout(s: QuoteSnapshot) -> Signal | None:
    """Simple day-trade rules: momentum + volume + breakout bias."""
    vol_r = _vol_ratio(s)
    reasons: list[str] = []
    score = 0.0

    # Strong up day with volume
    if s.change_pct >= 1.2:
        score += 2.0
        reasons.append(f"صعود يومي {s.change_pct:.1f}%")
    elif s.change_pct >= 0.5:
        score += 1.0
        reasons.append(f"صعود خفيف {s.change_pct:.1f}%")

    if vol_r >= 1.5:
        score += 2.0
        reasons.append(f"حجم أعلى من المعتاد ×{vol_r:.1f}")
    elif vol_r >= 1.1:
        score += 0.8
        reasons.append(f"حجم فوق المتوسط ×{vol_r:.1f}")

    # Near day high = breakout continuation bias
    if s.day_high > 0 and (s.last / s.day_high) >= 0.985:
        score += 1.5
        reasons.append("قرب قمة اليوم (زخم استمرار)")

    if s.above_vwap_proxy:
        score += 0.7
        reasons.append("فوق متوسط السعر المرجعي")

    if 45 <= s.rsi_14 <= 70:
        score += 1.0
        reasons.append(f"RSI مناسب {s.rsi_14:.0f}")
    elif s.rsi_14 > 78:
        score -= 1.5
        reasons.append(f"RSI مرتفع جدًا {s.rsi_14:.0f} — خطر مطاردة")

    # Gap chase penalty
    if s.gap_pct >= 3.0 and s.change_pct < s.gap_pct:
        score -= 1.0
        reasons.append(f"فجوة افتتاح كبيرة {s.gap_pct:.1f}% — حذر من المطاردة")

    # Weak / down hard
    if s.change_pct <= -1.5 and vol_r >= 1.2:
        stop = round(s.last * 1.012, 2)
        target = round(s.last * 0.985, 2)
        return Signal(
            symbol=s.symbol,
            action=Action.AVOID,
            score=score,
            reason="ضعف واضح مع حجم — لا تناسب شراء يومي للمبتدئ",
            entry_hint=s.last,
            stop_hint=stop,
            target_hint=target,
            side="none",
        )

    # Position hints for long
    entry = round(s.last, 2)
    stop = round(min(s.day_low, s.last * 0.988), 2)
    risk = max(entry - stop, entry * 0.008)
    target = round(entry + risk * 1.8, 2)

    # Long alerts only on non-negative days (unless strong bounce setup later)
    if s.change_pct < 0:
        action = Action.WAIT
        reasons.append("السهم بالسالب اليوم — لا شراء يومي")
        score = min(score, 2.5)
    elif score >= 5.0:
        action = Action.CONSIDER_LONG
    elif score >= 3.2:
        action = Action.WATCH_ENTRY
    elif s.rsi_14 > 78 and s.change_pct > 2:
        action = Action.TAKE_PROFIT_ZONE
        reasons.append("امتداد قوي — الأفضل انتظار تراجع صغير")
    else:
        action = Action.WAIT
        if not reasons:
            reasons.append("لا توجد شروط زخم كافية الآن")

    return Signal(
        symbol=s.symbol,
        action=action,
        score=round(score, 2),
        reason=" | ".join(reasons),
        entry_hint=entry,
        stop_hint=stop,
        target_hint=target,
        side="long" if action in (Action.CONSIDER_LONG, Action.WATCH_ENTRY) else "none",
    )


def rank_signals(snapshots: list[QuoteSnapshot]) -> list[Signal]:
    signals: list[Signal] = []
    for snap in snapshots:
        sig = score_momentum_breakout(snap)
        if sig:
            signals.append(sig)
    # Prefer actionable first, then score
    priority = {
        Action.CONSIDER_LONG: 0,
        Action.WATCH_ENTRY: 1,
        Action.TAKE_PROFIT_ZONE: 2,
        Action.WAIT: 3,
        Action.AVOID: 4,
    }
    signals.sort(key=lambda x: (priority[x.action], -x.score))
    return signals

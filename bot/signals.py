from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from bot.config import Settings
from bot.market_data import QuoteSnapshot, market_context, sector_momentum
from bot.strategies import StrategyHit, evaluate_all


class Action(str, Enum):
    WATCH_ENTRY = "راقب دخول"
    CONSIDER_LONG = "فكّر في شراء قصير المدى"
    CONSIDER_SHORT = "فكّر في بيع قصير بحذر"
    AVOID = "تجنّب الآن"
    TAKE_PROFIT_ZONE = "منطقة جني أرباح / لا تطارد"
    WAIT = "انتظر تأكيد"
    NO_TRADE_DAY = "لا تتداول اليوم"


@dataclass
class Signal:
    symbol: str
    action: Action
    score: float  # legacy 0-10ish for compatibility
    score_100: int  # 33 composite 0-100
    reason: str
    entry_hint: float
    stop_hint: float
    target_hint: float
    side: str
    strategies: list[str] = field(default_factory=list)
    strategy_notes: list[str] = field(default_factory=list)


def _clamp(n: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, n))


def compose_signal(
    s: QuoteSnapshot,
    hits: list[StrategyHit],
    *,
    no_trade_today: bool = False,
) -> Signal:
    # Long-only: تجاهل أي نقاط شورت متبقية وحولها لتجنب
    hits = [
        StrategyHit(h.name, h.name_ar, h.points, "avoid", h.note)
        if h.side == "short"
        else h
        for h in hits
    ]
    long_pts = sum(h.points for h in hits if h.side == "long")
    avoid_pts = sum(h.points for h in hits if h.side == "avoid")
    neutral_pts = sum(h.points for h in hits if h.side == "neutral")

    # 33 composite 0-100 — بدون شورت
    raw = long_pts * 1.1 + neutral_pts * 0.3 - avoid_pts * 1.2
    score_100 = int(_clamp(50 + raw, 0, 100))

    names = [h.name_ar for h in hits]
    notes = [f"{h.name_ar}: {h.note}" for h in hits]

    entry = round(s.last, 2)
    if long_pts > 0:
        stop = round(min(s.day_low, s.support or s.last * 0.988, s.last * 0.988), 2)
        if stop >= entry:
            stop = round(entry * 0.988, 2)
        risk = max(entry - stop, entry * 0.008)
        # هدف أول واقعي للمضاربة ≈ 1.2R (كان 1.8R وغالباً بعيد)
        raw_target = entry + risk * 1.2
        if s.resistance and s.resistance > entry:
            target = round(max(raw_target, min(s.resistance, entry + risk * 1.8)), 2)
        else:
            target = round(raw_target, 2)
        side = "long"
    else:
        stop = round(s.last * 0.99, 2)
        target = round(s.last * 1.01, 2)
        side = "none"

    if no_trade_today:
        action = Action.NO_TRADE_DAY
        side = "none"
    elif avoid_pts >= 12 or (s.news_negative and avoid_pts >= 8):
        action = Action.AVOID
        side = "none"
    elif side == "long" and score_100 >= 68 and long_pts >= 14:
        action = Action.CONSIDER_LONG
    elif side == "long" and score_100 >= 58:
        action = Action.WATCH_ENTRY
    elif s.rsi_14 >= 78 and s.change_pct > 2:
        action = Action.TAKE_PROFIT_ZONE
        side = "none"
    else:
        action = Action.WAIT

    # legacy score ~0-10 from score_100
    legacy = round(score_100 / 10.0, 2)
    reason = " | ".join(notes[:6]) if notes else "لا إشارات استراتيجية قوية"

    return Signal(
        symbol=s.symbol,
        action=action,
        score=legacy,
        score_100=score_100,
        reason=reason,
        entry_hint=entry,
        stop_hint=stop,
        target_hint=target,
        side=side if action in (Action.CONSIDER_LONG, Action.WATCH_ENTRY) else "none",
        strategies=names,
        strategy_notes=notes,
    )


def rank_signals(
    snapshots: list[QuoteSnapshot],
    *,
    settings: Settings | None = None,
    vol_preference: str | None = None,
) -> list[Signal]:
    ctx = market_context()
    sec = sector_momentum(snapshots)
    spy_chg = ctx.get("details", {}).get("SPY", {}).get("change_pct", 0.0)
    qqq_chg = ctx.get("details", {}).get("QQQ", {}).get("change_pct", 0.0)
    no_trade = bool(ctx.get("no_trade_today"))

    if vol_preference is None:
        vol_preference = "low" if (settings and settings.is_beginner) else "high"

    signals: list[Signal] = []
    for snap in snapshots:
        hits = evaluate_all(
            snap,
            sector_mom=sec,
            spy_chg=spy_chg,
            qqq_chg=qqq_chg,
            vol_preference=vol_preference,
        )
        sig = compose_signal(snap, hits, no_trade_today=no_trade and snap.symbol in ("SPY", "QQQ", "IWM"))
        # Propagate no-trade as market banner via index symbols only;
        # for others still score but dampen longs if no_trade
        if no_trade and sig.side == "long" and sig.action in (Action.CONSIDER_LONG, Action.WATCH_ENTRY):
            sig.action = Action.WAIT
            sig.side = "none"
            sig.reason = "لا تتداول اليوم (ظروف سوق) — " + sig.reason
            sig.strategies = ["لا تتداول اليوم"] + sig.strategies
        signals.append(sig)

    # Ensure a market-level NO_TRADE signal exists when flagged
    if no_trade:
        reasons = "، ".join(ctx.get("no_trade_reasons") or ["ظروف سوق صعبة"])
        signals.insert(
            0,
            Signal(
                symbol="MARKET",
                action=Action.NO_TRADE_DAY,
                score=0,
                score_100=0,
                reason=f"إشارة عامة: لا تتداول اليوم — {reasons}",
                entry_hint=0,
                stop_hint=0,
                target_hint=0,
                side="none",
                strategies=["لا تتداول اليوم"],
                strategy_notes=[reasons],
            ),
        )

    priority = {
        Action.NO_TRADE_DAY: 0,
        Action.CONSIDER_LONG: 1,
        Action.CONSIDER_SHORT: 2,
        Action.WATCH_ENTRY: 3,
        Action.TAKE_PROFIT_ZONE: 4,
        Action.WAIT: 5,
        Action.AVOID: 6,
    }
    signals.sort(key=lambda x: (priority.get(x.action, 9), -x.score_100))
    return signals


def track_strategy_hits(settings: Settings, signals: list[Signal]) -> Path:
    """35 — append daily strategy hit counts for later comparison."""
    path = settings.data_dir / "strategy_performance.json"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    day = data.setdefault(today, {})
    for sig in signals:
        for name in sig.strategies:
            bucket = day.setdefault(name, {"hits": 0, "longish": 0, "shortish": 0})
            bucket["hits"] += 1
            if sig.side == "long":
                bucket["longish"] += 1
            if sig.side == "short":
                bucket["shortish"] += 1
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def compare_strategies(settings: Settings, a: str, b: str) -> str:
    """35 compare two strategy hit frequencies (proxy until PnL linked)."""
    path = settings.data_dir / "strategy_performance.json"
    if not path.exists():
        return "لا توجد بيانات مقارنة بعد — انتظر بضعة أيام تداول."
    data = json.loads(path.read_text(encoding="utf-8"))
    tot_a = tot_b = 0
    days = 0
    for day, bucket in data.items():
        days += 1
        tot_a += (bucket.get(a) or {}).get("hits", 0)
        tot_b += (bucket.get(b) or {}).get("hits", 0)
    return (
        f"مقارنة تقريبية عبر {days} يوم:\n"
        f"• {a}: {tot_a} ظهور\n"
        f"• {b}: {tot_b} ظهور\n"
        "ملاحظة: هذا عدّاد إشارات وليس أرباحًا محققة بعد."
    )

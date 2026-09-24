"""Smart signal intelligence layers (confidence, traps, VWAP, targets, expiry)."""

from __future__ import annotations

from typing import Any

from bot.liquidity import liquidity_dict
from bot.market_data import QuoteSnapshot
from bot.signals import Action, Signal


CANDLE_DRAW = {
    "مطرقة": "🔨 ┴█",
    "ابتلاع صاعد": "📗 █▼→██",
    "ابتلاع هابط": "📕 ██→█▼",
    "دوجي": "➕ ─●─",
    "نجمة الصباح": "🌅 ··█",
    "نجمة المساء": "🌇 █··",
}


def _vwap_dist_pct(snap: QuoteSnapshot) -> float | None:
    if not snap or not snap.vwap_proxy or snap.vwap_proxy <= 0:
        return None
    return ((snap.last - snap.vwap_proxy) / snap.vwap_proxy) * 100


def _vol_ratio(snap: QuoteSnapshot) -> float:
    if not snap or snap.avg_volume_20 <= 0:
        return 1.0
    return snap.volume / snap.avg_volume_20


def confidence_grade(score_100: int, *, flags: dict[str, Any]) -> tuple[str, str]:
    """21 — A/B/C confidence from score + risk flags."""
    penalties = 0
    if flags.get("fake_liquidity"):
        penalties += 2
    if flags.get("near_earnings"):
        penalties += 2
    if flags.get("wait_1m_confirm"):
        penalties += 1
    if flags.get("vwap_divergence") and flags.get("vwap_side") == "below":
        penalties += 1
    if flags.get("qqq_drag"):
        penalties += 1
    if flags.get("expired"):
        return "C", "منتهية / ضعيفة"
    adj = score_100 - penalties * 8
    if adj >= 78 and penalties <= 1:
        return "A", "ثقة عالية"
    if adj >= 62:
        return "B", "ثقة متوسطة"
    return "C", "ثقة منخفضة — راقب فقط"


def detect_fake_liquidity(snap: QuoteSnapshot) -> tuple[bool, str]:
    """23 — volume spike without real price expansion = trap."""
    vr = _vol_ratio(snap)
    rng = float(snap.range_pct or 0)
    atr = float(snap.atr_pct or 0)
    chg = abs(float(snap.change_pct or 0))
    if vr >= 2.2 and chg < 0.45 and rng < max(0.55, atr * 0.35):
        return True, f"حجم ×{vr:.1f} بلا توسّع سعري (مدى {rng:.2f}%) — سبايك وهمي محتمل"
    if vr >= 3.0 and chg < 0.7 and rng < 0.9:
        return True, f"سيولة منفوخة ×{vr:.1f} مع حركة ضعيفة"
    return False, ""


def detect_wait_1m_confirm(snap: QuoteSnapshot, side: str) -> tuple[bool, str]:
    """22 — ask for one more minute confirmation before chasing."""
    if side != "long":
        return False, ""
    vr = _vol_ratio(snap)
    dist = _vwap_dist_pct(snap)
    # Jumping without hold above VWAP / open
    if snap.change_pct >= 0.9 and snap.last < snap.open * 1.001:
        return True, "الصعود لم يثبت فوق الافتتاح — انتظر تأكيد دقيقة"
    if dist is not None and -0.15 <= dist <= 0.35 and vr >= 1.4 and snap.change_pct >= 0.5:
        return True, "عند VWAP مع حجم — انتظر تأكيد دقيقة فوقه"
    if snap.change_pct >= 1.2 and vr < 1.05:
        return True, "تحرك سريع بسيولة عادية — انتظر تأكيد دقيقة"
    return False, ""


def vwap_alert(snap: QuoteSnapshot, side: str) -> dict[str, Any]:
    """24 — divergence / stretch from VWAP."""
    dist = _vwap_dist_pct(snap)
    if dist is None:
        return {"active": False}
    if side == "long" and dist <= -1.0:
        return {
            "active": True,
            "side": "below",
            "dist_pct": round(dist, 2),
            "ar": f"تحت VWAP بـ {abs(dist):.2f}% — ضعف نسبي",
        }
    if side == "long" and dist >= 1.8:
        return {
            "active": True,
            "side": "stretched",
            "dist_pct": round(dist, 2),
            "ar": f"ممتد فوق VWAP +{dist:.2f}% — لا تطارد",
        }
    if -0.4 <= dist <= 0.5 and side == "long":
        return {
            "active": True,
            "side": "near",
            "dist_pct": round(dist, 2),
            "ar": f"قرب VWAP ({dist:+.2f}%) — منطقة قرار",
        }
    return {"active": False, "dist_pct": round(dist, 2), "side": "ok"}


def candle_draw(snap: QuoteSnapshot) -> dict[str, str]:
    """25 — simple drawn candle pattern label."""
    name = (snap.candle_pattern or "").strip()
    if not name or name in ("", "لا نمط"):
        # derive crude body from open/last/day range
        if snap.open <= 0:
            return {"name": "", "draw": "", "ar": ""}
        body = snap.last - snap.open
        rng = max(snap.day_high - snap.day_low, snap.last * 0.001)
        if abs(body) / rng < 0.15:
            name = "دوجي"
        elif body > 0 and (snap.open - snap.day_low) > abs(body) * 1.5:
            name = "مطرقة"
        elif body > 0:
            name = "ابتلاع صاعد"
        elif body < 0:
            name = "ابتلاع هابط"
    draw = CANDLE_DRAW.get(name, "│█│")
    return {"name": name, "draw": draw, "ar": f"شمعة: {name} {draw}".strip()}


def alt_stop_plan(entry: float, stop: float, snap: QuoteSnapshot, side: str = "long") -> dict[str, Any]:
    """26 — alternative ATR-based stop scenario."""
    atr_pct = max(float(snap.atr_pct or 0), 1.0)
    if side == "long":
        atr_stop = round(entry * (1 - atr_pct / 100 * 0.55), 2)
        struct = float(stop)
        # tighter of structure vs ATR band, but not tighter than 0.5%
        alt = max(min(struct, atr_stop), round(entry * 0.995, 2))
        if alt >= entry:
            alt = round(entry * 0.992, 2)
        return {
            "stop_alt": alt,
            "stop_main": round(struct, 2),
            "ar": f"وقف بديل≈ ${alt} (ATR) · الرئيسي≈ ${struct}",
        }
    atr_stop = round(entry * (1 + atr_pct / 100 * 0.55), 2)
    return {"stop_alt": atr_stop, "stop_main": round(float(stop), 2), "ar": f"وقف بديل≈ ${atr_stop}"}


def partial_target_plan(entry: float, target: float, stop: float, side: str = "long") -> dict[str, Any]:
    """27 — take 50% at midpoint, trail remainder."""
    if side != "long" or entry <= 0:
        return {}
    mid = round(entry + (target - entry) * 0.5, 2)
    trail = round(max(entry * 0.004, (entry - stop) * 0.35), 2)
    return {
        "target_partial": mid,
        "target_final": round(target, 2),
        "trail_offset": trail,
        "ar": f"جني 50% عند ${mid} ثم وقف متحرك ≈ ${trail} تحت القمة",
    }


def qqq_correlation_flag(snap: QuoteSnapshot, qqq_chg: float) -> dict[str, Any]:
    """28 — high QQQ beta when Nasdaq is weak = drag risk."""
    corr = float(snap.corr_qqq or 0)
    if corr >= 0.7 and qqq_chg <= -0.6 and snap.change_pct >= 0:
        return {
            "active": True,
            "qqq_drag": True,
            "corr": round(corr, 2),
            "ar": f"ارتباط QQQ مرتفع ({corr:.2f}) والمؤشر {qqq_chg:+.2f}% — خطر سحب",
        }
    if corr >= 0.75 and qqq_chg >= 0.5 and snap.change_pct >= 0.4:
        return {
            "active": True,
            "qqq_drag": False,
            "corr": round(corr, 2),
            "ar": f"يتماشى مع QQQ ({corr:.2f} · {qqq_chg:+.2f}%)",
        }
    return {"active": False, "corr": round(corr, 2)}


def near_earnings(snap: QuoteSnapshot, days: int = 2) -> tuple[bool, str]:
    """29 — auto-flag / exclude when earnings are imminent."""
    d = snap.days_to_earnings
    if d is None:
        return False, ""
    if d <= days:
        return True, f"أرباح خلال {d} يوم — استبعد أو قلّل الحجم"
    if d <= 5:
        return False, f"أرباح بعد {d} يوم — راقب التقلب"
    return False, ""


def opportunity_expired(
    snap: QuoteSnapshot,
    *,
    entry: float,
    side: str,
    action: Action,
) -> tuple[bool, str]:
    """30 — setup already ran; don't chase."""
    if side != "long" or entry <= 0:
        return False, ""
    stretch = ((snap.last - entry) / entry) * 100
    # Already extended from suggested entry
    if stretch >= 1.2 and action == Action.CONSIDER_LONG:
        return True, f"فات الدخول — السعر أعلى من الدخول بـ {stretch:.2f}%"
    # Vertical spike day
    if snap.change_pct >= 4.5 and snap.rsi_14 >= 78:
        return True, "امتداد مفرط (RSI مرتفع) — الفرصة انتهت للمطاردة"
    dist = _vwap_dist_pct(snap)
    if dist is not None and dist >= 2.5 and snap.change_pct >= 2.0:
        return True, "ممتد جداً فوق VWAP — لا تدخل متأخراً"
    return False, ""


def enrich_smart_signal(
    sig: Signal,
    snap: QuoteSnapshot | None,
    *,
    qqq_chg: float = 0.0,
    live_last: float | None = None,
) -> dict[str, Any]:
    """Build smart-intel payload for dashboard / telegram cards."""
    if snap is None:
        return {
            "confidence": "C",
            "confidence_ar": "غير كافٍ",
            "tags": [],
            "expired": True,
            "exclude": True,
            "exclude_reason": "لا لقطة سعر",
        }

    last = float(live_last if live_last and live_last > 0 else snap.last)
    entry = float(sig.entry_hint or last)
    stop = float(sig.stop_hint or last * 0.99)
    target = float(sig.target_hint or last * 1.02)
    side = sig.side or "long"

    fake, fake_ar = detect_fake_liquidity(snap)
    wait, wait_ar = detect_wait_1m_confirm(snap, side)
    earn_block, earn_ar = near_earnings(snap, days=2)
    expired, expired_ar = opportunity_expired(snap, entry=entry, side=side, action=sig.action)
    vwap = vwap_alert(snap, side)
    qqq = qqq_correlation_flag(snap, qqq_chg)
    candle = candle_draw(snap)
    alt = alt_stop_plan(entry, stop, snap, side=side)
    partial = partial_target_plan(entry, target, stop, side=side)
    liq = liquidity_dict(snap)

    flags = {
        "fake_liquidity": fake,
        "wait_1m_confirm": wait,
        "near_earnings": earn_block,
        "vwap_divergence": bool(vwap.get("active") and vwap.get("side") in ("below", "stretched")),
        "vwap_side": vwap.get("side"),
        "qqq_drag": bool(qqq.get("qqq_drag")),
        "expired": expired,
    }
    grade, grade_ar = confidence_grade(int(sig.score_100 or 0), flags=flags)

    tags: list[dict[str, str]] = []
    if grade:
        tags.append({"key": "confidence", "ar": f"ثقة {grade}", "tone": "ok" if grade == "A" else ("warn" if grade == "B" else "bad")})
    if wait:
        tags.append({"key": "wait_1m", "ar": "انتظار تأكيد دقيقة", "tone": "warn"})
    if fake:
        tags.append({"key": "fake_liq", "ar": "فخ سيولة؟", "tone": "bad"})
    if vwap.get("active"):
        tone = "bad" if vwap.get("side") in ("below", "stretched") else "ok"
        tags.append({"key": "vwap", "ar": "VWAP", "tone": tone})
    if candle.get("name"):
        tags.append({"key": "candle", "ar": candle["name"], "tone": "ok"})
    if qqq.get("active"):
        tags.append({"key": "qqq", "ar": "QQQ", "tone": "bad" if qqq.get("qqq_drag") else "ok"})
    if earn_block:
        tags.append({"key": "earn", "ar": "قرب أرباح", "tone": "bad"})
    if expired:
        tags.append({"key": "expired", "ar": "الفرصة انتهت", "tone": "bad"})

    # Earnings / severe fake-liq junk are hard drops. "Expired" still shows as watch-only.
    exclude = bool(earn_block or (fake and grade == "C"))
    exclude_reason = earn_ar or (fake_ar if fake else "")

    notes = [x for x in (wait_ar, fake_ar, vwap.get("ar"), qqq.get("ar"), earn_ar, expired_ar, candle.get("ar"), alt.get("ar"), partial.get("ar")) if x]

    return {
        "confidence": grade,
        "confidence_ar": grade_ar,
        "tags": tags,
        "wait_1m_confirm": wait,
        "wait_1m_ar": wait_ar,
        "fake_liquidity": fake,
        "fake_liquidity_ar": fake_ar,
        "vwap": vwap,
        "candle": candle,
        "stop_alt": alt.get("stop_alt"),
        "stop_main": alt.get("stop_main", stop),
        "stop_plan_ar": alt.get("ar", ""),
        "target_partial": partial.get("target_partial"),
        "target_final": partial.get("target_final", target),
        "trail_offset": partial.get("trail_offset"),
        "partial_plan_ar": partial.get("ar", ""),
        "qqq": qqq,
        "near_earnings": earn_block,
        "earnings_ar": earn_ar,
        "expired": expired,
        "expired_ar": expired_ar,
        "exclude": exclude,
        "exclude_reason": exclude_reason,
        "notes": notes[:6],
        "rvol": liq.get("rvol"),
    }

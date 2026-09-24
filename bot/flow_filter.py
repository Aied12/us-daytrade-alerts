"""Day-trade flow filter: require real momentum + liquidity (no slow tickers)."""

from __future__ import annotations

from datetime import datetime, time as dtime
from typing import Any
from zoneinfo import ZoneInfo

from bot.liquidity import liquidity_dict
from bot.live_quotes import session_phase
from bot.market_data import QuoteSnapshot

NY = ZoneInfo("America/New_York")

# Floors tuned so early RTH mega-liquid movers still pass
MIN_DOLLAR_VOL = 15_000_000  # $15M traded so far today
MIN_AVG_DOLLAR = 12_000_000  # typical daily $ volume
MIN_RVOL_PACE_PRE = 1.05
MIN_RVOL_PACE_RTH = 0.95
MIN_CHG_PRE = 0.40
MIN_CHG_RTH = 0.45  # was 0.85 — too strict on quiet tape
MIN_ATR_PCT = 1.0
MIN_RANGE_PCT = 0.45


def _early_rth(now: datetime | None = None) -> bool:
    now = now or datetime.now(NY)
    t = now.time()
    return dtime(9, 30) <= t < dtime(11, 0)


def flow_thresholds(phase: str | None = None, now: datetime | None = None) -> dict[str, float]:
    phase = phase or session_phase(now)
    now = now or datetime.now(NY)
    if phase == "pre":
        return {
            "min_dollar": float(MIN_DOLLAR_VOL * 0.25),
            "min_avg_dollar": float(MIN_AVG_DOLLAR),
            "min_rvol_pace": float(MIN_RVOL_PACE_PRE),
            "min_chg": float(MIN_CHG_PRE),
            "min_atr": float(MIN_ATR_PCT),
            "min_range": 0.25,
        }
    if phase == "post":
        return {
            "min_dollar": float(MIN_DOLLAR_VOL * 0.8),
            "min_avg_dollar": float(MIN_AVG_DOLLAR),
            "min_rvol_pace": 0.9,
            "min_chg": 0.55,
            "min_atr": float(MIN_ATR_PCT),
            "min_range": 0.5,
        }
    # Regular: soften further in first 90 minutes (volume still building)
    early = _early_rth(now)
    return {
        "min_dollar": float(MIN_DOLLAR_VOL * (0.55 if early else 1.0)),
        "min_avg_dollar": float(MIN_AVG_DOLLAR),
        "min_rvol_pace": float(0.85 if early else MIN_RVOL_PACE_RTH),
        "min_chg": float(0.35 if early else MIN_CHG_RTH),
        "min_atr": float(MIN_ATR_PCT),
        "min_range": float(0.35 if early else MIN_RANGE_PCT),
    }


def passes_daytrade_flow(snap: QuoteSnapshot | None, phase: str | None = None) -> tuple[bool, str]:
    """Return (ok, reason_ar). Reject quiet / illiquid / low-ATR names."""
    if snap is None or snap.last <= 0:
        return False, "لا سعر"
    now = datetime.now(NY)
    phase = phase or session_phase(now)
    th = flow_thresholds(phase, now=now)
    liq = liquidity_dict(snap, now=now)
    rvol = float(liq.get("rvol") or 0)
    rvol_pace = float(liq.get("rvol_pace") or 0)
    dollar = float(liq.get("dollar_volume") or 0)
    avg_dollar = float(liq.get("avg_dollar_volume") or 0)
    chg = float(snap.change_pct or 0)
    atr = float(getattr(snap, "atr_pct", 0) or 0)
    rng = float(getattr(snap, "range_pct", 0) or 0)
    grade = liq.get("grade")

    # Absolute momentum floor (long bias: need green)
    if chg < th["min_chg"]:
        return False, f"زخم ضعيف ({chg:+.2f}% < {th['min_chg']}%)"

    # Liquidity: pass if EITHER printed $ today OR typical daily $ is solid
    liquid_enough = dollar >= th["min_dollar"] or avg_dollar >= th["min_avg_dollar"]
    if not liquid_enough:
        return False, "سيولة يومية ضعيفة"

    # Mega printed dollar → skip raw RVOL traps early in the day
    mega_print = dollar >= 40_000_000 or (avg_dollar >= 80_000_000 and dollar >= 12_000_000)
    if not mega_print:
        if dollar < th["min_dollar"] and rvol_pace < th["min_rvol_pace"]:
            return False, f"تداول بطيء اليوم (${dollar/1e6:.0f}M · وتيرة×{rvol_pace:.2f})"
        if rvol_pace < th["min_rvol_pace"] and dollar < th["min_dollar"] * 2.5:
            return False, f"وتيرة حجم منخفضة ×{rvol_pace:.2f} (RVOL خام ×{rvol:.2f})"

    if atr > 0 and atr < th["min_atr"] and abs(chg) < th["min_chg"] * 1.8 and not mega_print:
        return False, f"ATR ضعيف ({atr:.1f}%) — حركة يومية محدودة"
    if rng > 0 and rng < th["min_range"] and rvol_pace < th["min_rvol_pace"] * 1.15 and not mega_print:
        return False, f"مدى اليوم ضيق ({rng:.2f}%)"

    # Only reject weak grade when neither pace nor dollar is supportive
    if grade == "weak" and not mega_print and rvol_pace < 1.0:
        return False, "درجة سيولة ضعيفة"
    return True, "زخم+سيولة مناسبان"


def momentum_rank_score(row: dict[str, Any] | QuoteSnapshot) -> float:
    """Higher = better day-trade candidate (momentum × participation)."""
    if isinstance(row, QuoteSnapshot):
        chg = abs(float(row.change_pct or 0))
        liq = liquidity_dict(row)
        rvol = float(liq.get("rvol_pace") or liq.get("rvol") or 0)
        dollar = float(liq.get("dollar_volume") or 0)
        atr = float(row.atr_pct or 0)
    else:
        chg = abs(float(row.get("change_pct") or 0))
        rvol = float(
            row.get("rvol_pace")
            or row.get("rvol")
            or (row.get("liquidity") or {}).get("rvol_pace")
            or (row.get("liquidity") or {}).get("rvol")
            or 0
        )
        dollar = float(
            row.get("dollar_volume")
            or (row.get("liquidity") or {}).get("dollar_volume")
            or 0
        )
        atr = float(row.get("atr_pct") or 0)
    dol_score = min(dollar / 50_000_000.0, 8.0)
    return chg * max(rvol, 0.5) * (1.0 + dol_score * 0.35) * (1.0 + min(atr, 6.0) * 0.05)


def gainer_passes_flow(row: dict[str, Any], phase: str | None = None) -> bool:
    """Filter screener gainers that are too quiet despite green %."""
    now = datetime.now(NY)
    phase = phase or session_phase(now)
    th = flow_thresholds(phase, now=now)
    chg = float(row.get("change_pct") or 0)
    dollar = float(row.get("dollar_volume") or 0)
    if chg < th["min_chg"]:
        return False
    if dollar < th["min_dollar"]:
        return False
    return True

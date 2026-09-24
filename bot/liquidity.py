"""Liquidity metrics for day-trade filtering (RVOL, dollar volume, grade)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from bot.market_data import QuoteSnapshot

NY = ZoneInfo("America/New_York")

# Regular session length in minutes (09:30–16:00)
RTH_MINUTES = 390.0
# Premarket window used for pace (04:00–09:30)
PRE_MINUTES = 330.0


@dataclass
class LiquidityInfo:
    rvol: float
    rvol_pace: float
    volume: float
    avg_volume_20: float
    dollar_volume: float
    avg_dollar_volume: float
    grade: str  # strong | medium | weak
    grade_ar: str
    label: str


def _fmt_money(n: float) -> str:
    abs_n = abs(n)
    if abs_n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B$"
    if abs_n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M$"
    if abs_n >= 1_000:
        return f"{n / 1_000:.0f}K$"
    return f"{n:.0f}$"


def session_elapsed_frac(now: datetime | None = None) -> float:
    """Expected share of full-day volume already printed (rough pace)."""
    now = now or datetime.now(NY)
    t = now.time()
    if t < dtime(4, 0):
        return 0.02
    if t < dtime(9, 30):
        # Premarket: map 04:00–09:30 onto ~8% of a typical day
        mins = (now.hour * 60 + now.minute) - (4 * 60)
        return max(0.02, min(0.10, (mins / PRE_MINUTES) * 0.10))
    if t < dtime(16, 0):
        mins = (now.hour * 60 + now.minute) - (9 * 60 + 30)
        # Front-load open: first hour often ~25–30% of day volume
        raw = mins / RTH_MINUTES
        if mins <= 60:
            return max(0.04, min(0.30, 0.04 + (mins / 60.0) * 0.26))
        return max(0.30, min(1.0, raw))
    return 1.0


def liquidity_info(snap: QuoteSnapshot | None, now: datetime | None = None) -> LiquidityInfo:
    if snap is None or snap.last <= 0:
        return LiquidityInfo(
            rvol=0.0,
            rvol_pace=0.0,
            volume=0.0,
            avg_volume_20=0.0,
            dollar_volume=0.0,
            avg_dollar_volume=0.0,
            grade="weak",
            grade_ar="ضعيف",
            label="سيولة: غير متاحة",
        )

    avg_vol = snap.avg_volume_20 if snap.avg_volume_20 > 0 else 0.0
    rvol = (snap.volume / avg_vol) if avg_vol > 0 else 1.0
    frac = session_elapsed_frac(now)
    # Pace-adjusted: 1.0 = on track for average day; >1.2 = elevated for time of day
    rvol_pace = rvol / max(frac, 0.03)
    dollar = snap.last * snap.volume
    avg_dollar = snap.last * avg_vol

    # Grade: dollar participation first (mega names look "weak" on raw RVOL early)
    if dollar >= 150_000_000 or (avg_dollar >= 100_000_000 and dollar >= 25_000_000):
        grade, grade_ar = "strong", "قوي"
    elif dollar >= 50_000_000 or (avg_dollar >= 50_000_000 and rvol_pace >= 0.9):
        grade, grade_ar = "strong", "قوي"
    elif dollar >= 20_000_000 or (avg_dollar >= 25_000_000 and rvol_pace >= 1.0):
        grade, grade_ar = "medium", "متوسط"
    elif avg_dollar >= 15_000_000 and (rvol_pace >= 1.1 or dollar >= 8_000_000):
        grade, grade_ar = "medium", "متوسط"
    elif rvol_pace >= 1.5 and dollar >= 5_000_000:
        grade, grade_ar = "medium", "متوسط"
    else:
        grade, grade_ar = "weak", "ضعيف"

    label = (
        f"RVOL ×{rvol:.1f} · وتيرة ×{rvol_pace:.1f} · اليوم {_fmt_money(dollar)} · متوسط {_fmt_money(avg_dollar)}"
    )
    return LiquidityInfo(
        rvol=round(rvol, 2),
        rvol_pace=round(rvol_pace, 2),
        volume=float(snap.volume),
        avg_volume_20=float(avg_vol),
        dollar_volume=round(dollar, 2),
        avg_dollar_volume=round(avg_dollar, 2),
        grade=grade,
        grade_ar=grade_ar,
        label=label,
    )


def liquidity_dict(snap: QuoteSnapshot | None, now: datetime | None = None) -> dict:
    info = liquidity_info(snap, now=now)
    return {
        "rvol": info.rvol,
        "rvol_pace": info.rvol_pace,
        "volume": info.volume,
        "avg_volume_20": info.avg_volume_20,
        "dollar_volume": info.dollar_volume,
        "avg_dollar_volume": info.avg_dollar_volume,
        "dollar_volume_label": _fmt_money(info.dollar_volume),
        "avg_dollar_volume_label": _fmt_money(info.avg_dollar_volume),
        "grade": info.grade,
        "grade_ar": info.grade_ar,
        "label": info.label,
    }

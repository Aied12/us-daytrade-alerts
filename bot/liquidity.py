"""Liquidity metrics for day-trade filtering (RVOL, dollar volume, grade)."""

from __future__ import annotations

from dataclasses import dataclass

from bot.market_data import QuoteSnapshot


@dataclass
class LiquidityInfo:
    rvol: float
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


def liquidity_info(snap: QuoteSnapshot | None) -> LiquidityInfo:
    if snap is None or snap.last <= 0:
        return LiquidityInfo(
            rvol=0.0,
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
    dollar = snap.last * snap.volume
    avg_dollar = snap.last * avg_vol

    # Tuned for ~45k SAR day trading: prefer liquid names
    if avg_dollar >= 80_000_000 and rvol >= 1.3:
        grade, grade_ar = "strong", "قوي"
    elif avg_dollar >= 80_000_000 and rvol >= 0.9:
        grade, grade_ar = "strong", "قوي"
    elif avg_dollar >= 25_000_000 and rvol >= 1.5:
        grade, grade_ar = "strong", "قوي"
    elif avg_dollar >= 20_000_000 and rvol >= 1.0:
        grade, grade_ar = "medium", "متوسط"
    elif avg_dollar >= 10_000_000 and rvol >= 0.8:
        grade, grade_ar = "medium", "متوسط"
    else:
        grade, grade_ar = "weak", "ضعيف"

    label = f"RVOL ×{rvol:.1f} · اليوم {_fmt_money(dollar)} · متوسط {_fmt_money(avg_dollar)}"
    return LiquidityInfo(
        rvol=round(rvol, 2),
        volume=float(snap.volume),
        avg_volume_20=float(avg_vol),
        dollar_volume=round(dollar, 2),
        avg_dollar_volume=round(avg_dollar, 2),
        grade=grade,
        grade_ar=grade_ar,
        label=label,
    )


def liquidity_dict(snap: QuoteSnapshot | None) -> dict:
    info = liquidity_info(snap)
    return {
        "rvol": info.rvol,
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

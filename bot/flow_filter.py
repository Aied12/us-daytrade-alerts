"""Day-trade flow filter: require real momentum + liquidity (no slow tickers)."""

from __future__ import annotations

from typing import Any

from bot.liquidity import liquidity_dict
from bot.live_quotes import session_phase
from bot.market_data import QuoteSnapshot


# Hard floors for "يمكن المضاربة عليه اليوم"
MIN_DOLLAR_VOL = 40_000_000  # $40M traded today
MIN_AVG_DOLLAR = 25_000_000  # typical daily $ volume
MIN_RVOL_PRE = 1.15
MIN_RVOL_RTH = 1.25
MIN_CHG_PRE = 0.55  # % vs prior close / RTH ref
MIN_CHG_RTH = 0.85
MIN_ATR_PCT = 1.4  # enough daily range to move
MIN_RANGE_PCT = 0.7  # today's high-low / prev


def flow_thresholds(phase: str | None = None) -> dict[str, float]:
    phase = phase or session_phase()
    if phase == "pre":
        return {
            "min_dollar": float(MIN_DOLLAR_VOL * 0.35),  # premarket volume builds slowly
            "min_avg_dollar": float(MIN_AVG_DOLLAR),
            "min_rvol": float(MIN_RVOL_PRE),
            "min_chg": float(MIN_CHG_PRE),
            "min_atr": float(MIN_ATR_PCT),
            "min_range": 0.35,
        }
    if phase == "post":
        return {
            "min_dollar": float(MIN_DOLLAR_VOL * 0.7),
            "min_avg_dollar": float(MIN_AVG_DOLLAR),
            "min_rvol": 1.1,
            "min_chg": 0.7,
            "min_atr": float(MIN_ATR_PCT),
            "min_range": 0.6,
        }
    return {
        "min_dollar": float(MIN_DOLLAR_VOL),
        "min_avg_dollar": float(MIN_AVG_DOLLAR),
        "min_rvol": float(MIN_RVOL_RTH),
        "min_chg": float(MIN_CHG_RTH),
        "min_atr": float(MIN_ATR_PCT),
        "min_range": float(MIN_RANGE_PCT),
    }


def passes_daytrade_flow(snap: QuoteSnapshot | None, phase: str | None = None) -> tuple[bool, str]:
    """Return (ok, reason_ar). Reject quiet / illiquid / low-ATR names."""
    if snap is None or snap.last <= 0:
        return False, "لا سعر"
    phase = phase or session_phase()
    th = flow_thresholds(phase)
    liq = liquidity_dict(snap)
    rvol = float(liq.get("rvol") or 0)
    dollar = float(liq.get("dollar_volume") or 0)
    avg_dollar = float(liq.get("avg_dollar_volume") or 0)
    chg = float(snap.change_pct or 0)
    atr = float(getattr(snap, "atr_pct", 0) or 0)
    rng = float(getattr(snap, "range_pct", 0) or 0)

    if chg < th["min_chg"]:
        return False, f"زخم ضعيف ({chg:+.2f}% < {th['min_chg']}%)"
    if avg_dollar < th["min_avg_dollar"] and dollar < th["min_dollar"]:
        return False, "سيولة يومية ضعيفة"
    if dollar < th["min_dollar"] and rvol < th["min_rvol"]:
        return False, f"تداول بطيء اليوم (${dollar/1e6:.0f}M · RVOL×{rvol:.2f})"
    if rvol < th["min_rvol"] and dollar < th["min_dollar"] * 2:
        return False, f"RVOL منخفض ×{rvol:.2f}"
    if atr > 0 and atr < th["min_atr"] and abs(chg) < th["min_chg"] * 1.5:
        return False, f"ATR ضعيف ({atr:.1f}%) — حركة يومية محدودة"
    if rng > 0 and rng < th["min_range"] and rvol < th["min_rvol"] * 1.2:
        return False, f"مدى اليوم ضيق ({rng:.2f}%)"
    if liq.get("grade") == "weak":
        return False, "درجة سيولة ضعيفة"
    return True, "زخم+سيولة مناسبان"


def momentum_rank_score(row: dict[str, Any] | QuoteSnapshot) -> float:
    """Higher = better day-trade candidate (momentum × participation)."""
    if isinstance(row, QuoteSnapshot):
        chg = abs(float(row.change_pct or 0))
        liq = liquidity_dict(row)
        rvol = float(liq.get("rvol") or 0)
        dollar = float(liq.get("dollar_volume") or 0)
        atr = float(row.atr_pct or 0)
    else:
        chg = abs(float(row.get("change_pct") or 0))
        rvol = float(row.get("rvol") or (row.get("liquidity") or {}).get("rvol") or 0)
        dollar = float(
            row.get("dollar_volume")
            or (row.get("liquidity") or {}).get("dollar_volume")
            or 0
        )
        atr = float(row.get("atr_pct") or 0)
    # log-ish dollar participation without math.log dependency edge cases
    dol_score = min(dollar / 50_000_000.0, 8.0)
    return chg * max(rvol, 0.5) * (1.0 + dol_score * 0.35) * (1.0 + min(atr, 6.0) * 0.05)


def gainer_passes_flow(row: dict[str, Any], phase: str | None = None) -> bool:
    """Filter screener gainers that are too quiet despite green %."""
    phase = phase or session_phase()
    th = flow_thresholds(phase)
    chg = float(row.get("change_pct") or 0)
    dollar = float(row.get("dollar_volume") or 0)
    if chg < th["min_chg"]:
        return False
    # Gainers board: need meaningful dollar flow (pre softer)
    if dollar < th["min_dollar"]:
        return False
    return True

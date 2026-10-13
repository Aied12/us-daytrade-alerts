"""شموع الدقيقة (Yahoo) لمتابعة الصفقات الورقية بدقة + فلتر عدم المطاردة (وضع مراقبة).

Bars are ``[ts, open, high, low, close, volume]`` with ``ts`` = bar start (epoch sec).
"""

from __future__ import annotations

from typing import Any

import requests

from bot.cacheutil import cached_call

UA = {"User-Agent": "Mozilla/5.0 us-daytrade-alerts/minute-bars"}
BARS_CACHE_TTL = 30

# فلتر عدم المطاردة — من تحليل 28/9–13/10 (وضع مراقبة فقط، لا يمنع الدخول)
CHASE_MAX_RUN15_PCT = 15.0
CHASE_MAX_RUN5_PCT = 8.0
CHASE_MAX_CHG_PCT = 100.0


def _fetch(symbol: str) -> list[list[float]] | None:
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "1d", "interval": "1m", "includePrePost": "true"},
            headers=UA,
            timeout=12,
        )
        r.raise_for_status()
        res = ((r.json().get("chart") or {}).get("result") or [None])[0]
        if not res:
            return None
        q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
        out: list[list[float]] = []
        for t, o, h, lo, c, v in zip(
            res.get("timestamp") or [],
            q.get("open") or [],
            q.get("high") or [],
            q.get("low") or [],
            q.get("close") or [],
            q.get("volume") or [],
        ):
            if None in (t, o, h, lo, c):
                continue
            out.append([int(t), float(o), float(h), float(lo), float(c), float(v or 0)])
        return out or None
    except Exception:
        return None


def fetch_minute_bars(symbol: str) -> list[list[float]] | None:
    sym = str(symbol or "").upper().strip()
    if not sym:
        return None
    try:
        hit = cached_call(f"bars1m:{sym}", lambda: _fetch(sym), ttl=BARS_CACHE_TTL)
        return hit if hit else None
    except Exception:
        return _fetch(sym)


def completed_bars(bars: list[list[float]] | None, now: float) -> list[list[float]]:
    """Only bars whose minute has fully closed (the live bar is still forming)."""
    return [b for b in (bars or []) if int(b[0]) + 60 <= now]


def anti_chase_check(
    bars: list[list[float]] | None,
    *,
    entry: float,
    at_ts: float,
    change_pct: float | None = None,
) -> dict[str, Any]:
    """Would the anti-chase rule have accepted this entry? (shadow — never blocks)."""
    pre = [b for b in (bars or []) if int(b[0]) + 60 <= at_ts]
    if entry <= 0 or len(pre) < 5:
        return {"shadow_filter_ok": None, "shadow_reason": "بيانات غير كافية"}
    c5 = pre[-5][4]
    c15 = pre[-15][4] if len(pre) >= 15 else pre[0][4]
    run5 = round((entry - c5) / c5 * 100.0, 2) if c5 > 0 else None
    run15 = round((entry - c15) / c15 * 100.0, 2) if c15 > 0 else None
    reasons: list[str] = []
    if run15 is not None and run15 >= CHASE_MAX_RUN15_PCT:
        reasons.append(f"ارتفع {run15:.0f}% آخر 15د")
    if run5 is not None and run5 >= CHASE_MAX_RUN5_PCT:
        reasons.append(f"ارتفع {run5:.0f}% آخر 5د")
    if change_pct is not None and float(change_pct) >= CHASE_MAX_CHG_PCT:
        reasons.append(f"تمدد يومي {float(change_pct):.0f}%")
    return {
        "shadow_filter_ok": not reasons,
        "shadow_run5_pct": run5,
        "shadow_run15_pct": run15,
        "shadow_reason": " · ".join(reasons) if reasons else "مقبول — بدون مطاردة",
    }

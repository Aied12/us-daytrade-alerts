from __future__ import annotations

"""94 US market holidays helper (extendable)."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

# Common US market holidays 2025-2027 (observed where applicable)
US_MARKET_HOLIDAYS = {
    # 2025
    date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17), date(2025, 4, 18),
    date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4), date(2025, 9, 1),
    date(2025, 11, 27), date(2025, 12, 25),
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    # 2027
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
}

# Early close days (1:00 PM ET) — informational
US_EARLY_CLOSE = {
    date(2025, 7, 3), date(2025, 11, 28), date(2025, 12, 24),
    date(2026, 11, 27), date(2026, 12, 24),
    date(2027, 11, 26),
}


def is_trading_day(now: datetime | None = None) -> bool:
    now = now or datetime.now(NY)
    d = now.date()
    if now.weekday() >= 5:
        return False
    return d not in US_MARKET_HOLIDAYS


def holiday_note(now: datetime | None = None) -> str | None:
    now = now or datetime.now(NY)
    d = now.date()
    if d in US_MARKET_HOLIDAYS:
        return f"اليوم عطلة سوق أمريكية ({d.isoformat()}) — لا تداول منتظم."
    if d in US_EARLY_CLOSE:
        return f"إغلاق مبكر محتمل اليوم ({d.isoformat()}) حوالي 1:00 م ET."
    return None

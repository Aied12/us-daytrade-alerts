"""End-of-day paper performance: flatten 23:00 Riyadh, archive, keep cumulative.

System rules (adopted):
- 23:00 Asia/Riyadh → close every open paper trade
- Write dated day log under data/day_performance/archive for later analysis
- Dashboard "today" KPIs clear after EOD the same evening; all-time cumulative stays
- New paper opens blocked from 23:00 until next Riyadh calendar day
"""

from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
RIYADH = ZoneInfo("Asia/Riyadh")
STATE_PATH = ROOT / "data" / "performance_state.json"
ARCHIVE_DIR = ROOT / "data" / "day_performance" / "archive"
FLAT_HOUR = 23  # 11:00 PM Saudi


def _now_riyadh() -> datetime:
    return datetime.now(RIYADH)


def riyadh_day(ts: float | None = None) -> str:
    if ts is None:
        return _now_riyadh().strftime("%Y-%m-%d")
    return datetime.fromtimestamp(float(ts), RIYADH).strftime("%Y-%m-%d")


def past_flat_time(now: datetime | None = None) -> bool:
    """True from 23:00 through 23:59 Riyadh."""
    now = now or _now_riyadh()
    return now.timetz().replace(tzinfo=None) >= dtime(FLAT_HOUR, 0)


def block_new_opens(now: datetime | None = None) -> bool:
    """No new paper opens after the 11pm flat until the next calendar day."""
    now = now or _now_riyadh()
    return past_flat_time(now)


def load_state() -> dict[str, Any]:
    try:
        if STATE_PATH.exists():
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = dict(state)
    state["updated_ts"] = int(time.time())
    state["updated_local"] = _now_riyadh().strftime("%Y-%m-%d %H:%M %Z")
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def today_kpis_cleared(now: datetime | None = None) -> bool:
    """After EOD archive tonight, today panel stays empty (cumulative untouched)."""
    now = now or _now_riyadh()
    day = now.strftime("%Y-%m-%d")
    st = load_state()
    return bool(st.get("archived_day") == day and past_flat_time(now))


def archive_day(day: str | None = None) -> dict[str, Any]:
    """Freeze day JSON/CSV into archive/ for later analysis. Idempotent per day."""
    from bot.day_performance import write_day_report

    day = day or riyadh_day()
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    report = write_day_report(day)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    (ARCHIVE_DIR / f"{day}.json").write_text(payload, encoding="utf-8")
    # CSV from published copy if present
    for src_name in (f"day-performance-{day}.csv", "day-performance.csv"):
        for folder in (ROOT / "docs", ROOT / "pages", ROOT / "data" / "day_performance"):
            src = folder / src_name
            if src.exists():
                shutil.copy2(src, ARCHIVE_DIR / f"{day}.csv")
                break
        if (ARCHIVE_DIR / f"{day}.csv").exists():
            break
    # Index pointer
    index_path = ARCHIVE_DIR / "index.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
    except Exception:
        index = []
    if not isinstance(index, list):
        index = []
    entry = {
        "day": day,
        "archived_local": _now_riyadh().strftime("%Y-%m-%d %H:%M %Z"),
        "json": f"/day-performance/archive/{day}.json",
        "summary": report.get("summary"),
        "note_ar": "أرشيف يومي للتحليل — التراكمي في السجل الحي لم يُصفَّر",
    }
    index = [e for e in index if not (isinstance(e, dict) and e.get("day") == day)]
    index.append(entry)
    index.sort(key=lambda e: str(e.get("day") or ""), reverse=True)
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    # Publish archive index for the dashboard
    for d in (ROOT / "docs", ROOT / "pages"):
        d.mkdir(parents=True, exist_ok=True)
        arch = d / "day-performance-archive"
        arch.mkdir(parents=True, exist_ok=True)
        (arch / f"{day}.json").write_text(payload, encoding="utf-8")
        if (ARCHIVE_DIR / f"{day}.csv").exists():
            shutil.copy2(ARCHIVE_DIR / f"{day}.csv", arch / f"{day}.csv")
        (arch / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def run_eod_if_needed(*, price_by_symbol: dict[str, float] | None = None) -> dict[str, Any]:
    """If Riyadh ≥ 23:00: flatten opens once, archive day, clear today panel flag.

    Cumulative ledger history is never wiped.
    """
    now = _now_riyadh()
    day = now.strftime("%Y-%m-%d")
    out: dict[str, Any] = {
        "day": day,
        "past_flat": past_flat_time(now),
        "closed_n": 0,
        "archived": False,
        "already_done": False,
    }
    if not past_flat_time(now):
        return out

    st = load_state()
    if st.get("eod_flat_day") == day and st.get("archived_day") == day:
        out["already_done"] = True
        return out

    # Mark-to-market with prices then flatten
    try:
        from bot.strategy_tracker import force_close_all_open, mark_to_market

        if price_by_symbol:
            mark_to_market(price_by_symbol)
        closed_n = force_close_all_open(reason_ar="إغلاق 11 مساءً")
        out["closed_n"] = int(closed_n)
    except Exception as e:
        out["close_error"] = str(e)

    try:
        archive_day(day)
        out["archived"] = True
    except Exception as e:
        out["archive_error"] = str(e)

    st["eod_flat_day"] = day
    st["archived_day"] = day
    st["today_panel_cleared"] = True
    st["note_ar"] = (
        f"تم إغلاق {out.get('closed_n', 0)} صفقة الساعة 11 مساءً {day} · "
        "الأرشيف محفوظ · صفحة اليوم مُصفَّرة · التراكمي كما هو"
    )
    save_state(st)
    out["state"] = st
    return out

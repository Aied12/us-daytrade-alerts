"""End-of-day paper performance: flatten 23:00 Riyadh, archive, keep cumulative.

System rules (adopted):
- 23:00 Asia/Riyadh → close every open paper trade
- Write dated day log under data/day_performance/archive for later analysis
- Dashboard "today" KPIs clear after EOD until next session (11:00); cumulative stays
- New paper opens blocked from 23:00 until 11:00 next Riyadh morning
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
SESSION_OPEN_HOUR = 11  # next morning — US premarket window starts ~11:00 SA


def _now_riyadh() -> datetime:
    return datetime.now(RIYADH)


def riyadh_day(ts: float | None = None) -> str:
    if ts is None:
        return _now_riyadh().strftime("%Y-%m-%d")
    return datetime.fromtimestamp(float(ts), RIYADH).strftime("%Y-%m-%d")


def past_flat_time(now: datetime | None = None) -> bool:
    """True from 23:00 through 23:59 Riyadh (trigger window for EOD)."""
    now = now or _now_riyadh()
    return now.timetz().replace(tzinfo=None) >= dtime(FLAT_HOUR, 0)


def in_overnight_lock(now: datetime | None = None) -> bool:
    """23:00 → 10:59 next morning: no new opens, keep page zeroed, flatten leftovers."""
    now = now or _now_riyadh()
    t = now.timetz().replace(tzinfo=None)
    return t >= dtime(FLAT_HOUR, 0) or t < dtime(SESSION_OPEN_HOUR, 0)


def block_new_opens(now: datetime | None = None) -> bool:
    """No new paper opens after 11pm flat until 11:00 next morning."""
    return in_overnight_lock(now)


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
    """After EOD: today panel empty until next session open (11:00). Cumulative untouched."""
    now = now or _now_riyadh()
    st = load_state()
    archived = str(st.get("archived_day") or "")
    today = now.strftime("%Y-%m-%d")
    # Same evening after flat
    if archived == today and past_flat_time(now):
        return True
    # Morning after archived day — keep zeroed until 11:00
    if archived and archived < today and now.timetz().replace(tzinfo=None) < dtime(SESSION_OPEN_HOUR, 0):
        return True
    # Overnight lock: always show cleared panel (no active session)
    if in_overnight_lock(now):
        return True
    return False


def archive_day(day: str | None = None) -> dict[str, Any]:
    """Freeze day JSON/CSV into archive/ for later analysis. Idempotent per day."""
    from bot.day_performance import write_day_report

    day = day or riyadh_day()
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    report = write_day_report(day)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    (ARCHIVE_DIR / f"{day}.json").write_text(payload, encoding="utf-8")
    for src_name in (f"day-performance-{day}.csv", "day-performance.csv"):
        for folder in (ROOT / "docs", ROOT / "pages", ROOT / "data" / "day_performance"):
            src = folder / src_name
            if src.exists():
                shutil.copy2(src, ARCHIVE_DIR / f"{day}.csv")
                break
        if (ARCHIVE_DIR / f"{day}.csv").exists():
            break
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
    for d in (ROOT / "docs", ROOT / "pages"):
        d.mkdir(parents=True, exist_ok=True)
        arch = d / "day-performance-archive"
        arch.mkdir(parents=True, exist_ok=True)
        (arch / f"{day}.json").write_text(payload, encoding="utf-8")
        if (ARCHIVE_DIR / f"{day}.csv").exists():
            shutil.copy2(ARCHIVE_DIR / f"{day}.csv", arch / f"{day}.csv")
        (arch / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def purge_invalid_overnight_trades() -> int:
    """Remove paper trades opened during the overnight lock (should never have opened)."""
    from bot.strategy_tracker import LEDGER_PATH, summarize

    try:
        data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except Exception:
        return 0
    trades = list(data.get("trades") or [])
    keep: list[dict[str, Any]] = []
    removed = 0
    for t in trades:
        opened_local = str(t.get("opened_local") or "")
        ots = t.get("opened_ts")
        try:
            if ots:
                odt = datetime.fromtimestamp(float(ots), RIYADH)
            elif len(opened_local) >= 16:
                odt = datetime.strptime(opened_local[:16], "%Y-%m-%d %H:%M").replace(tzinfo=RIYADH)
            else:
                keep.append(t)
                continue
        except Exception:
            keep.append(t)
            continue
        th = odt.timetz().replace(tzinfo=None)
        if th >= dtime(FLAT_HOUR, 0) or th < dtime(SESSION_OPEN_HOUR, 0):
            removed += 1
            continue
        keep.append(t)
    if removed:
        data["trades"] = keep
        data["summary"] = summarize(keep)
        data["updated_ts"] = int(time.time())
        data["updated_local"] = _now_riyadh().strftime("%Y-%m-%d %H:%M %Z")
        LEDGER_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return removed


def run_eod_if_needed(*, price_by_symbol: dict[str, float] | None = None) -> dict[str, Any]:
    """If Riyadh ≥ 23:00: flatten opens once, archive day, clear today panel flag.

    During overnight lock (23:00–11:00): keep blocking opens, flatten leftovers, purge invalids.
    Cumulative ledger history is never wiped (except invalid overnight rows).
    """
    now = _now_riyadh()
    day = now.strftime("%Y-%m-%d")
    out: dict[str, Any] = {
        "day": day,
        "past_flat": past_flat_time(now),
        "overnight_lock": in_overnight_lock(now),
        "closed_n": 0,
        "archived": False,
        "already_done": False,
        "purged_n": 0,
    }

    if in_overnight_lock(now):
        try:
            out["purged_n"] = purge_invalid_overnight_trades()
        except Exception as e:
            out["purge_error"] = str(e)

    if not past_flat_time(now):
        if in_overnight_lock(now):
            try:
                from bot.strategy_tracker import force_close_all_open, mark_to_market

                if price_by_symbol:
                    mark_to_market(price_by_symbol)
                out["closed_n"] = int(force_close_all_open(reason_ar="إغلاق 11 مساءً"))
            except Exception as e:
                out["close_error"] = str(e)
        return out

    st = load_state()
    if st.get("eod_flat_day") == day and st.get("archived_day") == day:
        out["already_done"] = True
        return out

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
        "الأرشيف محفوظ · صفحة اليوم مُصفَّرة حتى 11 صباحاً · التراكمي كما هو"
    )
    save_state(st)
    out["state"] = st
    return out

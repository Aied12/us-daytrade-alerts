"""Paper-trade ledger: track scanned setups and measure strategy success.

Educational only — not live brokerage fills. Opens a paper trade when the
dashboard posts a long setup (خطط دخول / قنص / جمال) with entry+stop+TP,
then marks win/loss from subsequent live prices on each always-on tick.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = ROOT / "data" / "strategy_ledger.json"
RIYADH = ZoneInfo("Asia/Riyadh")
NY = ZoneInfo("America/New_York")

MAX_TRADES = 400
OPEN_MAX_SEC = 6 * 3600  # day-trade horizon inside an open session
MIN_HOLD_SEC = 90        # don't close instantly on same-tick noise
# Protection (from 28–30 Sep loss analysis): cancel قناص if never green
NO_GREEN_EXIT_SEC = 5 * 60  # 5 minutes
NO_GREEN_MFE_MAX = 0.0      # must have printed above entry (MFE > 0)
# اختراق: الثبات فوق مستوى الدخول 3–5 دقائق قبل اعتبار الصفقة مفتوحة
BREAKOUT_CONFIRM_SEC = 4 * 60
BREAKOUT_PENDING_MAX_SEC = 20 * 60
# Paper open gate for قناص — skip thin names that gap through stops
QANNAS_PAPER_MIN_DOLLAR = 1_000_000.0
QANNAS_PAPER_MIN_RVOL = 2.0
QANNAS_PAPER_MIN_PRICE = 0.50
QANNAS_PAPER_MAX_CHG_PCT = 120.0   # avoid parabolic late extensions
QANNAS_PAPER_LATE_HOUR = 17       # no new paper opens from 17:00 Asia/Riyadh

ACTIVE_STATUSES = ("open", "pending_breakout")


def _session_phase() -> str:
    """pre | regular | post | closed — US equity extended hours."""
    try:
        from bot.live_quotes import session_phase

        return str(session_phase() or "closed")
    except Exception:
        try:
            from bot.gainers import session_phase

            return str(session_phase() or "closed")
        except Exception:
            return "closed"


def _paper_session_open() -> bool:
    """Paper daytrades during US pre/regular/post, until 23:00 Riyadh flat."""
    try:
        from bot.performance_eod import block_new_opens

        if block_new_opens():
            return False
    except Exception:
        pass
    return _session_phase() in ("pre", "regular", "post")


def _riyadh_eod_flat() -> bool:
    """Flatten window: 23:00 flat trigger and whole overnight lock until 11:00."""
    try:
        from bot.performance_eod import in_overnight_lock, past_flat_time

        return bool(past_flat_time() or in_overnight_lock())
    except Exception:
        return False



def _attach_appearance(trade: dict[str, Any], row: dict[str, Any], source: str) -> None:
    """Stamp appear→open analysis fields onto a new paper trade."""
    try:
        from bot.day_performance import append_lifecycle_event, snapshot_from_row
    except Exception:
        snapshot_from_row = None  # type: ignore
        append_lifecycle_event = None  # type: ignore

    if snapshot_from_row is not None:
        snap = snapshot_from_row(row, source=source)
        for k, v in snap.items():
            if k == "source":
                continue
            if v is not None and v != "" and v != []:
                trade[k] = v
        if snap.get("appear_name"):
            trade["name"] = snap["appear_name"]
    # Fallbacks if board had no first_ts
    if not trade.get("appeared_ts"):
        trade["appeared_ts"] = int(trade.get("opened_ts") or time.time())
        trade["appeared_local"] = trade.get("opened_local") or ""
    if trade.get("appear_price") is None:
        trade["appear_price"] = trade.get("entry")
    opened = int(trade.get("opened_ts") or 0)
    appeared = int(trade.get("appeared_ts") or opened)
    # Sticky first_ts from a prior Riyadh day would inflate time_to_open
    if appeared and opened:
        try:
            a_day = datetime.fromtimestamp(appeared, RIYADH).strftime("%Y-%m-%d")
            o_day = datetime.fromtimestamp(opened, RIYADH).strftime("%Y-%m-%d")
            if a_day != o_day:
                trade["appeared_ts"] = opened
                trade["appeared_local"] = trade.get("opened_local") or ""
                appeared = opened
        except Exception:
            pass
    trade["time_to_open_sec"] = max(0, opened - appeared) if opened and appeared else 0
    trade["price_trail"] = [
        {
            "ts": opened,
            "local": trade.get("opened_local") or "",
            "px": trade.get("entry"),
            "pnl_pct": 0.0,
            "event": "open",
        }
    ]
    if append_lifecycle_event is not None:
        try:
            append_lifecycle_event(
                "open",
                {
                    "trade_id": trade.get("id"),
                    "symbol": trade.get("symbol"),
                    "source": source,
                    "entry": trade.get("entry"),
                    "stop": trade.get("stop"),
                    "tp1": trade.get("tp1"),
                    "tp2": trade.get("tp2"),
                    "appeared_ts": trade.get("appeared_ts"),
                    "appear_change_pct": trade.get("appear_change_pct"),
                    "appear_rvol": trade.get("appear_rvol"),
                    "appear_has_news": trade.get("appear_has_news"),
                },
            )
        except Exception:
            pass


def _now() -> float:
    return time.time()


def _px(n: float) -> float:
    n = float(n or 0)
    if n <= 0:
        return 0.0
    return round(n, 4 if n < 1 else 2)


def _load() -> dict[str, Any]:
    try:
        data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("trades"), list):
            return data
    except Exception:
        pass
    return {"trades": [], "updated_ts": 0, "summary": {}}


def _save(data: dict[str, Any]) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    trades = list(data.get("trades") or [])
    if len(trades) > MAX_TRADES:
        # Keep newest + still-active
        open_ones = [t for t in trades if t.get("status") in ACTIVE_STATUSES]
        closed = [t for t in trades if t.get("status") not in ACTIVE_STATUSES]
        closed = closed[-(MAX_TRADES - len(open_ones)) :]
        data["trades"] = closed + open_ones
    data["updated_ts"] = int(_now())
    data["updated_local"] = datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M %Z")
    LEDGER_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _trade_id(source: str, symbol: str, day: str) -> str:
    return f"{source}:{symbol.upper()}:{day}"


def _strategies_of(row: dict[str, Any], source: str) -> list[str]:
    out: list[str] = []
    raw = row.get("strategies") or []
    if isinstance(raw, list):
        out.extend(str(x) for x in raw if x)
    if source == "sniper":
        out.append("ماسح القنص")
        if row.get("tier_key") == "cents" or (row.get("last") or 0) < 1:
            out.append("قنص سنتات")
        else:
            out.append("قنص تحت $5")
        if row.get("has_news"):
            out.append("قنص + خبر")
    elif source == "qannas":
        out.append("القناص")
        if row.get("has_news"):
            out.append("القناص + محفز")
        if row.get("low_float"):
            out.append("فلوت ضيق")
    elif source == "jamal":
        out.append("استراتيجية جمال")
    elif source == "opps":
        out.append("خطط الدخول")
    # unique preserve order
    seen = set()
    uniq = []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq[:6]


def _levels_from_row(row: dict[str, Any]) -> tuple[float, float, float, float] | None:
    entry = float(
        row.get("entry")
        or row.get("entry_trigger")
        or row.get("last")
        or 0
    )
    stop = float(row.get("stop") or 0)
    tp1 = float(row.get("tp1") or row.get("target_partial") or row.get("target") or 0)
    tp2 = float(row.get("tp2") or row.get("target_final") or row.get("target") or tp1)
    if entry <= 0 or stop <= 0:
        return None
    if tp1 <= 0:
        # fallback 1.5R long
        risk = max(entry - stop, entry * 0.01)
        tp1 = entry + 1.5 * risk
    if tp2 <= 0:
        risk = max(entry - stop, entry * 0.01)
        tp2 = entry + 2.5 * risk
    # long-only ledger
    if stop >= entry:
        stop = entry * 0.98
    if tp1 <= entry:
        tp1 = entry * 1.02
    if tp2 < tp1:
        tp2 = tp1
    return _px(entry), _px(stop), _px(tp1), _px(tp2)


def ingest_candidates(
    *,
    opportunities: list[dict[str, Any]] | None = None,
    sniper: list[dict[str, Any]] | None = None,
    jamal: list[dict[str, Any]] | None = None,
    qannas: list[dict[str, Any]] | None = None,
) -> int:
    """Open paper trades for new long setups. Returns number newly opened."""
    # No new daytrades while US market (incl. after-hours) is shut
    if not _paper_session_open():
        return 0

    data = _load()
    trades: list[dict[str, Any]] = list(data.get("trades") or [])
    by_id = {str(t.get("id")): t for t in trades if t.get("id")}
    day = datetime.now(RIYADH).strftime("%Y-%m-%d")
    opened = 0
    now = _now()

    batches: list[tuple[str, list[dict[str, Any]]]] = [
        ("opps", opportunities or []),
        ("sniper", sniper or []),
        ("jamal", jamal or []),
        ("qannas", qannas or []),
    ]
    for source, rows in batches:
        for row in rows:
            sym = str(row.get("symbol") or "").upper().strip()
            if not sym:
                continue
            # Skip shorts / expired / rejected
            if (row.get("side") or "long") == "short":
                continue
            if row.get("expired"):
                continue
            if source == "opps" and row.get("allowed") is False:
                continue
            if source == "jamal":
                st = str(row.get("state") or row.get("status") or "").upper()
                # Only track when trigger/entry-ish, or watch with levels
                if st and st in ("EXIT_BEFORE_CLOSE", "STOPPED", "EXPIRED", "DONE"):
                    continue
            levels = _levels_from_row(row)
            if not levels:
                continue
            entry, stop, tp1, tp2 = levels
            tid = _trade_id(source, sym, day)
            existing = by_id.get(tid)
            if existing and existing.get("status") in ACTIVE_STATUSES:
                # Refresh live mark only (open or awaiting breakout confirm)
                last = float(row.get("last") or entry)
                existing["last"] = _px(last)
                existing["mfe_pct"] = max(float(existing.get("mfe_pct") or 0), _pct(entry, last))
                existing["mae_pct"] = min(float(existing.get("mae_pct") or 0), _pct(entry, last))
                continue
            if existing and existing.get("status") not in ACTIVE_STATUSES:
                continue  # already closed today
            # قناص only: tighten paper entry liquidity (rule 2)
            if source == "qannas" and not _qannas_paper_entry_ok(row):
                continue
            trade = {
                "id": tid,
                "symbol": sym,
                "name": str(row.get("name") or ""),
                "source": source,
                "source_ar": {
                    "opps": "خطط الدخول",
                    "sniper": "ماسح القنص",
                    "jamal": "استراتيجية جمال",
                    "qannas": "القناص",
                }.get(source, source),
                "strategies": _strategies_of(row, source),
                "side": "long",
                "appeared_signal_ts": int(now),
                "opened_ts": int(now),  # updated again when breakout confirms
                "opened_local": datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M"),
                "opened_tz": "Asia/Riyadh",
                "opened_label_ar": "توقيت السعودية",
                "entry": entry,
                "stop": stop,
                "initial_stop": stop,
                "tp1": tp1,
                "tp2": tp2,
                "last": _px(float(row.get("last") or entry)),
                # انتظار اختراق وتثبيت فوق الدخول 3–5د
                "status": "pending_breakout",
                "breakout_level": entry,
                "breakout_above_since": None,
                "stop_trail_stage": 0,  # 0=أولي · 1=وقف=دخول بعد هدف1 · 2=وقف=هدف1 بعد هدف2
                "exit": None,
                "exit_ts": None,
                "exit_local": None,
                "pnl_pct": None,
                "r_multiple": None,
                "mfe_pct": 0.0,
                "mae_pct": 0.0,
                "result_ar": "انتظار اختراق (تثبيت 3–5د)",
            }
            _attach_appearance(trade, row, source)
            trades.append(trade)
            by_id[tid] = trade
            opened += 1
    data["trades"] = trades
    data["summary"] = summarize(trades)
    _save(data)
    return opened


def _pct(entry: float, last: float) -> float:
    if entry <= 0:
        return 0.0
    return round((last - entry) / entry * 100.0, 3)


def _r_multiple(entry: float, stop: float, exit_px: float) -> float:
    risk = entry - stop
    if risk <= 0:
        return 0.0
    return round((exit_px - entry) / risk, 3)


def _qannas_paper_entry_ok(row: dict[str, Any]) -> bool:
    """قناص paper: فقط مكتمل 4/4 + سيولة/وقت/سقف تمدد."""
    # Scanner marks complete only when all 4 checklist conditions pass (incl. news)
    if not bool(row.get("complete")):
        return False
    dollar = float(row.get("dollar_volume") or row.get("appear_dollar_volume") or 0)
    rvol = float(row.get("rvol") or row.get("appear_rvol") or 0)
    chg = float(row.get("change_pct") or row.get("appear_change_pct") or 0)
    last = float(row.get("last") or row.get("entry") or row.get("appear_price") or 0)
    if dollar < QANNAS_PAPER_MIN_DOLLAR:
        return False
    if rvol < QANNAS_PAPER_MIN_RVOL and chg < 40.0:
        return False
    if last > 0 and last < QANNAS_PAPER_MIN_PRICE:
        return False
    if chg >= QANNAS_PAPER_MAX_CHG_PCT:
        return False
    try:
        h = datetime.now(RIYADH).hour
        if h >= QANNAS_PAPER_LATE_HOUR:
            return False
    except Exception:
        pass
    return True


def mark_to_market(price_by_symbol: dict[str, float]) -> dict[str, Any]:
    """Update active trades; breakout confirm, trail stops, SL / session / timeout."""
    data = _load()
    trades: list[dict[str, Any]] = list(data.get("trades") or [])
    now = _now()
    session_closed = not _paper_session_open()
    for t in trades:
        status = str(t.get("status") or "")
        if status not in ACTIVE_STATUSES:
            continue
        sym = str(t.get("symbol") or "").upper()
        entry = float(t.get("entry") or 0)
        stop = float(t.get("stop") or 0)
        initial_stop = float(t.get("initial_stop") or stop or 0)
        if not t.get("initial_stop") and stop > 0:
            t["initial_stop"] = _px(stop)
            initial_stop = stop
        tp1 = float(t.get("tp1") or 0)
        tp2 = float(t.get("tp2") or 0)
        last = float(price_by_symbol.get(sym) or t.get("last") or entry)
        if last <= 0 or entry <= 0:
            continue
        t["last"] = _px(last)
        pnl = _pct(entry, last)
        t["mfe_pct"] = max(float(t.get("mfe_pct") or 0), pnl)
        t["mae_pct"] = min(float(t.get("mae_pct") or 0), pnl)
        try:
            from bot.day_performance import append_price_trail

            append_price_trail(t, last)
        except Exception:
            pass

        # —— انتظار اختراق: يثبت فوق الدخول 3–5د بدون كسر ——
        if status == "pending_breakout":
            pending_age = now - float(t.get("appeared_signal_ts") or t.get("opened_ts") or now)
            if _riyadh_eod_flat() or session_closed:
                label = "إلغاء اختراق — إغلاق جلسة"
                if _riyadh_eod_flat():
                    label = "إغلاق 11 مساءً (قبل اختراق)"
                _close_trade(
                    t,
                    "session_end",
                    last,
                    label,
                    entry=entry,
                    stop=initial_stop or stop,
                    now=now,
                )
                continue
            # كسر الوقف قبل التأكيد → خروج فوري
            if stop > 0 and last <= stop:
                _close_trade(
                    t,
                    "loss_sl",
                    last,
                    "كسر الوقف قبل تأكيد الاختراق",
                    entry=entry,
                    stop=initial_stop or stop,
                    now=now,
                )
                continue
            if last >= entry:
                if not t.get("breakout_above_since"):
                    t["breakout_above_since"] = int(now)
                    t["result_ar"] = "اختراق جاري — تثبيت فوق الدخول"
                held = now - float(t.get("breakout_above_since") or now)
                if held >= BREAKOUT_CONFIRM_SEC:
                    t["status"] = "open"
                    t["opened_ts"] = int(now)
                    t["opened_local"] = datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M")
                    t["breakout_confirmed_ts"] = int(now)
                    t["result_ar"] = "مفتوح — اختراق مؤكد"
                    status = "open"
                else:
                    t["result_ar"] = f"تثبيت اختراق {int(held)}ث / {BREAKOUT_CONFIRM_SEC}ث"
                    continue
            else:
                # كسر مستوى الدخول أثناء التثبيت → إعادة العدّ
                t["breakout_above_since"] = None
                t["result_ar"] = "انتظار اختراق (تثبيت 3–5د)"
                if pending_age >= BREAKOUT_PENDING_MAX_SEC:
                    _close_trade(
                        t,
                        "breakout_fail",
                        last,
                        "فشل اختراق — ما ثبت فوق الدخول",
                        entry=entry,
                        stop=initial_stop or stop,
                        now=now,
                    )
                continue
            # fall through once promoted to open on this same tick

        age = now - float(t.get("opened_ts") or now)
        # Adopted system: flatten every open at 23:00 Asia/Riyadh (≈ US RTH close)
        if _riyadh_eod_flat():
            label = "إغلاق 11 مساءً +" if pnl >= 0 else "إغلاق 11 مساءً −"
            _close_trade(t, "session_end", last, label, entry=entry, stop=initial_stop or stop, now=now)
            continue
        if session_closed:
            label = "إغلاق الجلسة +" if pnl >= 0 else "إغلاق الجلسة −"
            _close_trade(t, "session_end", last, label, entry=entry, stop=initial_stop or stop, now=now)
            continue
        if age < MIN_HOLD_SEC:
            continue

        stage = int(t.get("stop_trail_stage") or 0)

        # Rule 1 (قناص): never printed green within 5 min → cancel early (before any trail)
        src = str(t.get("source") or "")
        mfe = float(t.get("mfe_pct") or 0)
        if (
            src == "qannas"
            and stage == 0
            and age >= NO_GREEN_EXIT_SEC
            and mfe <= NO_GREEN_MFE_MAX
            and pnl <= 0
        ):
            _close_trade(
                t,
                "loss_fade",
                last,
                "إلغاء — ما اخضرّت خلال 5د",
                entry=entry,
                stop=initial_stop or stop,
                now=now,
            )
            continue

        # تحقيق الأهداف → رفع الوقف (لا إغلاق فوري على الهدف)
        # هدف2: وقف → هدف1 | هدف1: وقف → سعر الدخول (مشاركة)
        if tp2 > 0 and last >= tp2 and stage < 2:
            t["stop"] = _px(tp1 if tp1 > 0 else entry)
            stop = float(t["stop"])
            t["stop_trail_stage"] = 2
            t["tp2_hit_ts"] = int(now)
            t["result_ar"] = "هدف2 ✓ — الوقف على هدف1"
            stage = 2
            try:
                from bot.day_performance import append_lifecycle_event

                append_lifecycle_event(
                    "trail",
                    {"trade_id": t.get("id"), "symbol": sym, "stage": 2, "stop": stop, "last": last},
                )
            except Exception:
                pass
        elif tp1 > 0 and last >= tp1 and stage < 1:
            t["stop"] = _px(entry)  # مشاركة
            stop = float(t["stop"])
            t["stop_trail_stage"] = 1
            t["tp1_hit_ts"] = int(now)
            t["result_ar"] = "هدف1 ✓ — الوقف على الدخول"
            stage = 1
            try:
                from bot.day_performance import append_lifecycle_event

                append_lifecycle_event(
                    "trail",
                    {"trade_id": t.get("id"), "symbol": sym, "stage": 1, "stop": stop, "last": last},
                )
            except Exception:
                pass

        # كسر الوقف → خروج فوري
        if stop > 0 and last <= stop:
            if stage >= 2:
                _close_trade(
                    t,
                    "win_trail_tp1",
                    last,
                    "خروج — كسر الوقف عند هدف1",
                    entry=entry,
                    stop=initial_stop or stop,
                    now=now,
                )
            elif stage >= 1:
                _close_trade(
                    t,
                    "win_be",
                    last,
                    "خروج — كسر الوقف عند الدخول (مشاركة)",
                    entry=entry,
                    stop=initial_stop or stop,
                    now=now,
                )
            else:
                _close_trade(
                    t,
                    "loss_sl",
                    last,
                    "كسر وقف الخسارة",
                    entry=entry,
                    stop=initial_stop or stop,
                    now=now,
                )
        elif age >= OPEN_MAX_SEC:
            label = "انتهى الوقت +" if pnl >= 0 else "انتهى الوقت −"
            _close_trade(t, "expired", last, label, entry=entry, stop=initial_stop or stop, now=now)

    data["trades"] = trades
    data["summary"] = summarize(trades)
    _save(data)
    return data["summary"]


def _close_trade(
    t: dict[str, Any],
    status: str,
    exit_px: float,
    label: str,
    *,
    entry: float,
    stop: float,
    now: float,
) -> None:
    t["status"] = status
    t["exit"] = _px(exit_px)
    t["exit_ts"] = int(now)
    t["exit_local"] = datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M")
    t["pnl_pct"] = _pct(entry, exit_px)
    # R على المخاطرة الأولية (قبل رفع الوقف)
    risk_stop = float(t.get("initial_stop") or stop or 0)
    t["r_multiple"] = _r_multiple(entry, risk_stop, exit_px)
    t["result_ar"] = label
    t["hold_sec"] = max(0, int(now) - int(t.get("opened_ts") or now))
    trail = t.get("price_trail")
    if not isinstance(trail, list):
        trail = []
        t["price_trail"] = trail
    trail.append(
        {
            "ts": int(now),
            "local": t["exit_local"],
            "px": _px(exit_px),
            "pnl_pct": t["pnl_pct"],
            "event": status,
        }
    )
    try:
        from bot.day_performance import append_lifecycle_event

        append_lifecycle_event(
            "close",
            {
                "trade_id": t.get("id"),
                "symbol": t.get("symbol"),
                "status": status,
                "entry": entry,
                "exit": t["exit"],
                "pnl_pct": t["pnl_pct"],
                "r_multiple": t["r_multiple"],
                "mfe_pct": t.get("mfe_pct"),
                "mae_pct": t.get("mae_pct"),
                "hold_sec": t.get("hold_sec"),
                "appeared_ts": t.get("appeared_ts"),
                "time_to_open_sec": t.get("time_to_open_sec"),
                "stop_trail_stage": t.get("stop_trail_stage"),
            },
        )
    except Exception:
        pass


def force_close_all_open(reason_ar: str = "إغلاق 11 مساءً") -> int:
    """Manually flatten every open/pending paper trade at last mark. Returns closed count."""
    data = _load()
    trades: list[dict[str, Any]] = list(data.get("trades") or [])
    now = _now()
    n = 0
    for t in trades:
        if t.get("status") not in ACTIVE_STATUSES:
            continue
        entry = float(t.get("entry") or 0)
        stop = float(t.get("initial_stop") or t.get("stop") or 0)
        last = float(t.get("last") or entry)
        if entry <= 0 or last <= 0:
            continue
        pnl = _pct(entry, last)
        label = f"{reason_ar} +" if pnl >= 0 else f"{reason_ar} −"
        _close_trade(t, "session_end", last, label, entry=entry, stop=stop, now=now)
        n += 1
    if n:
        data["trades"] = trades
        data["summary"] = summarize(trades)
        _save(data)
    return n


def _trade_day(t: dict[str, Any]) -> str:
    """Riyadh calendar day for a paper trade (opened_local / opened_ts / id)."""
    opened_local = str(t.get("opened_local") or "")
    if len(opened_local) >= 10 and opened_local[4] == "-" and opened_local[7] == "-":
        return opened_local[:10]
    ots = t.get("opened_ts")
    if ots:
        try:
            return datetime.fromtimestamp(float(ots), RIYADH).strftime("%Y-%m-%d")
        except Exception:
            pass
    tid = str(t.get("id") or "")
    parts = tid.split(":")
    if len(parts) >= 3 and len(parts[-1]) >= 10 and parts[-1][4] == "-":
        return parts[-1][:10]
    return ""


def _trades_for_day(trades: list[dict[str, Any]], day: str | None = None) -> list[dict[str, Any]]:
    day = day or datetime.now(RIYADH).strftime("%Y-%m-%d")
    return [t for t in trades if _trade_day(t) == day]


def _stop_distance_pct(entry: float, stop: float) -> float | None:
    """How far stop sits below entry (positive %). None if levels invalid."""
    entry = float(entry or 0)
    stop = float(stop or 0)
    if entry <= 0 or stop <= 0 or stop >= entry:
        return None
    return round((entry - stop) / entry * 100.0, 1)


def summarize(trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    trades = list(trades if trades is not None else (_load().get("trades") or []))
    closed = [t for t in trades if t.get("status") and t.get("status") not in ACTIVE_STATUSES]
    open_n = sum(1 for t in trades if t.get("status") in ACTIVE_STATUSES)
    pending_n = sum(1 for t in trades if t.get("status") == "pending_breakout")
    # Directional only: exclude flat session_end / 0% closes from win_rate & avg
    decided = [t for t in closed if t.get("pnl_pct") is not None]
    win_n = sum(1 for t in decided if float(t.get("pnl_pct") or 0) > 0)
    loss_n = sum(1 for t in decided if float(t.get("pnl_pct") or 0) < 0)
    flat_n = sum(1 for t in decided if float(t.get("pnl_pct") or 0) == 0)
    session_end_n = sum(1 for t in closed if t.get("status") == "session_end")
    directional = [t for t in decided if float(t.get("pnl_pct") or 0) != 0]
    avg_pnl = (
        round(sum(float(t.get("pnl_pct") or 0) for t in directional) / len(directional), 3)
        if directional
        else 0.0
    )
    avg_r = (
        round(
            sum(float(t.get("r_multiple") or 0) for t in directional if t.get("r_multiple") is not None)
            / max(1, len(directional)),
            3,
        )
        if directional
        else 0.0
    )
    scored = win_n + loss_n
    win_rate = round(100.0 * win_n / scored, 1) if scored else None

    by_source: dict[str, dict[str, Any]] = {}
    by_strategy: dict[str, dict[str, Any]] = {}

    def _acc(bucket: dict[str, Any], t: dict[str, Any]) -> None:
        bucket["n"] = int(bucket.get("n") or 0) + 1
        if t.get("status") in ACTIVE_STATUSES:
            bucket["open"] = int(bucket.get("open") or 0) + 1
            return
        bucket["closed"] = int(bucket.get("closed") or 0) + 1
        pnl = float(t.get("pnl_pct") or 0)
        if pnl != 0:
            bucket["pnl_sum"] = round(float(bucket.get("pnl_sum") or 0) + pnl, 3)
            bucket["pnl_n"] = int(bucket.get("pnl_n") or 0) + 1
        if pnl > 0:
            bucket["wins"] = int(bucket.get("wins") or 0) + 1
        elif pnl < 0:
            bucket["losses"] = int(bucket.get("losses") or 0) + 1
        else:
            bucket["flat"] = int(bucket.get("flat") or 0) + 1

    for t in trades:
        src = str(t.get("source_ar") or t.get("source") or "?")
        _acc(by_source.setdefault(src, {}), t)
        for name in t.get("strategies") or []:
            _acc(by_strategy.setdefault(str(name), {}), t)

    def _finalize(d: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        rows = []
        for name, b in d.items():
            closed_n = int(b.get("closed") or 0)
            wins_b = int(b.get("wins") or 0)
            losses_b = int(b.get("losses") or 0)
            decided_b = wins_b + losses_b
            pnl_n = int(b.get("pnl_n") or 0)
            rows.append(
                {
                    "name": name,
                    "n": int(b.get("n") or 0),
                    "open": int(b.get("open") or 0),
                    "closed": closed_n,
                    "wins": wins_b,
                    "losses": losses_b,
                    "flat": int(b.get("flat") or 0),
                    "win_rate": round(100.0 * wins_b / decided_b, 1) if decided_b else None,
                    "avg_pnl_pct": round(float(b.get("pnl_sum") or 0) / pnl_n, 3) if pnl_n else None,
                }
            )
        rows.sort(key=lambda r: (r["win_rate"] is not None, r["win_rate"] or -1, r["n"]), reverse=True)
        return rows

    recent_closed = sorted(
        [t for t in closed if t.get("exit_ts")],
        key=lambda t: int(t.get("exit_ts") or 0),
        reverse=True,
    )[:15]
    recent_open = [t for t in trades if t.get("status") in ACTIVE_STATUSES]
    # Newest opens last in ledger → show newest first, keep all (cap 40)
    recent_open = list(reversed(recent_open))[:40]

    return {
        "open": open_n,
        "pending_breakout": pending_n,
        "closed": len(closed),
        "wins": win_n,
        "losses": loss_n,
        "flat": flat_n,
        "session_end": session_end_n,
        "win_rate": win_rate,
        "avg_pnl_pct": avg_pnl,
        "avg_r": avg_r,
        "by_source": _finalize(by_source),
        "by_strategy": _finalize(by_strategy)[:20],
        "recent_closed": [
            {
                "symbol": t.get("symbol"),
                "source_ar": t.get("source_ar"),
                "strategies": (t.get("strategies") or [])[:3],
                "entry": t.get("entry"),
                "exit": t.get("exit"),
                "stop": t.get("stop"),
                "stop_pct": _stop_distance_pct(float(t.get("entry") or 0), float(t.get("stop") or 0)),
                "pnl_pct": t.get("pnl_pct"),
                "r_multiple": t.get("r_multiple"),
                "status": t.get("status"),
                "result_ar": t.get("result_ar"),
                "opened_local": t.get("opened_local"),
                "opened_ts": t.get("opened_ts"),
                "exit_local": t.get("exit_local"),
                "exit_ts": t.get("exit_ts"),
            }
            for t in recent_closed
        ],
        "recent_open": [
            {
                "symbol": t.get("symbol"),
                "source_ar": t.get("source_ar"),
                "strategies": (t.get("strategies") or [])[:3],
                "entry": t.get("entry"),
                "last": t.get("last"),
                "stop": t.get("stop"),
                "stop_pct": _stop_distance_pct(float(t.get("entry") or 0), float(t.get("stop") or 0)),
                "tp1": t.get("tp1"),
                "pnl_pct": _pct(float(t.get("entry") or 0), float(t.get("last") or 0)),
                "mfe_pct": t.get("mfe_pct"),
                "mae_pct": t.get("mae_pct"),
                "opened_local": t.get("opened_local"),
                "opened_ts": t.get("opened_ts"),
                "result_ar": "مفتوح",
            }
            for t in recent_open
        ],
        "note_ar": (
            "مؤشرات اليوم فقط · نسبة النجاح بدون إغلاق الجلسة 0% · "
            "صفقات جديدة وقف≈10% (المفتوحة القديمة قد تكون أوسع) · تعليمي."
        ),
    }


def sync_from_boards(
    *,
    opportunities: list[dict[str, Any]] | None = None,
    sniper: list[dict[str, Any]] | None = None,
    jamal: list[dict[str, Any]] | None = None,
    qannas: list[dict[str, Any]] | None = None,
    price_by_symbol: dict[str, float] | None = None,
) -> dict[str, Any]:
    """One-shot: ingest new setups, mark-to-market, return dashboard payload.

    Top-level KPIs are **today only** (Riyadh). All-time totals live under ``all_time``.
    At 23:00 Riyadh: flatten opens, archive the day, clear today panel (cumulative kept).
    """
    prices = dict(price_by_symbol or {})
    for rows in (opportunities or [], sniper or [], jamal or [], qannas or []):
        for r in rows:
            sym = str(r.get("symbol") or "").upper()
            last = float(r.get("last") or 0)
            if sym and last > 0:
                prices[sym] = last

    eod: dict[str, Any] = {}
    try:
        from bot.performance_eod import run_eod_if_needed

        eod = run_eod_if_needed(price_by_symbol=prices)
    except Exception as e:
        eod = {"error": str(e)}

    ingest_candidates(
        opportunities=opportunities,
        sniper=sniper,
        jamal=jamal,
        qannas=qannas,
    )
    mark_to_market(prices)
    data = _load()
    all_trades = list(data.get("trades") or [])
    # Always refresh all-time on disk summary from full ledger (never wiped by EOD)
    all_time = summarize(all_trades)
    data["summary"] = all_time
    _save(data)

    day = datetime.now(RIYADH).strftime("%Y-%m-%d")
    cleared = False
    try:
        from bot.performance_eod import today_kpis_cleared as _cleared

        cleared = bool(_cleared())
    except Exception:
        cleared = False

    if cleared:
        today = summarize([])
        today_note = (
            f"يوم {day} مؤرشف بعد إغلاق 11 مساءً — لوحة اليوم مُصفَّرة · "
            "التراكمي محفوظ · السجل في الأرشيف للتحليل"
        )
        today_trades_n = 0
    else:
        today = summarize(_trades_for_day(all_trades, day))
        today_note = (
            f"مؤشرات يوم {day} · إغلاق إجباري 11 مساءً السعودية · وقف جديد ≈10٪ · "
            "نسبة النجاح بدون إغلاق مسطّح 0٪ · تعليمي."
        )
        today_trades_n = len(_trades_for_day(all_trades, day))

    summary = {
        **today,
        "day": day,
        "scope": "today",
        "today_cleared": cleared,
        "eod": {
            "closed_n": eod.get("closed_n"),
            "archived": eod.get("archived"),
            "already_done": eod.get("already_done"),
            "past_flat": eod.get("past_flat"),
        },
        "all_time": {
            "open": all_time.get("open"),
            "closed": all_time.get("closed"),
            "wins": all_time.get("wins"),
            "losses": all_time.get("losses"),
            "flat": all_time.get("flat"),
            "win_rate": all_time.get("win_rate"),
            "avg_pnl_pct": all_time.get("avg_pnl_pct"),
            "avg_r": all_time.get("avg_r"),
        },
        "note_ar": today_note,
    }

    try:
        from bot.day_performance import write_day_report

        day_perf = write_day_report()
        if cleared:
            summary["day_performance"] = {
                "day": day,
                "summary": {
                    "appeared": 0,
                    "opened": 0,
                    "open_now": 0,
                    "closed": 0,
                    "wins": 0,
                    "losses": 0,
                    "win_rate": None,
                },
                "urls": {
                    "json": "/day-performance.json",
                    "csv": "/day-performance.csv",
                    "archive": f"/day-performance-archive/{day}.json",
                    "archive_index": "/day-performance-archive/index.json",
                },
                "trades_n": 0,
                "appeared_only_n": 0,
                "note_ar": today_note,
                "archived": True,
            }
        else:
            summary["day_performance"] = {
                "day": day_perf.get("day"),
                "summary": day_perf.get("summary"),
                "urls": {
                    **(day_perf.get("urls") or {}),
                    "archive_index": "/day-performance-archive/index.json",
                },
                "trades_n": len(day_perf.get("trades") or []),
                "appeared_only_n": len(day_perf.get("appeared_only") or []),
                "note_ar": day_perf.get("note_ar"),
            }
    except Exception as e:
        summary["day_performance"] = {"error": str(e)}

    return {
        "updated_ts": data.get("updated_ts"),
        "updated_local": data.get("updated_local"),
        "trades_n": len(all_trades),
        "trades_today_n": today_trades_n,
        **summary,
    }

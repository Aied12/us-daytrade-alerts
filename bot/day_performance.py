"""Daily performance lifecycle log: appear → open → close.

Builds an analysis-ready day file (JSON + CSV) for القناص paper trades.
Educational only — not brokerage fills.
"""

from __future__ import annotations

import csv
import io
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = ROOT / "data" / "strategy_ledger.json"
QANNAS_BOARD = ROOT / "data" / "qannas_board.json"
QANNAS_SEEN = ROOT / "data" / "qannas_seen.json"
SNIPER_BOARD = ROOT / "data" / "sniper_board.json"
SNIPER_SEEN = ROOT / "data" / "sniper_seen.json"
LIFECYCLE_DIR = ROOT / "data" / "lifecycle"
DAY_DIR = ROOT / "data" / "day_performance"
RIYADH = ZoneInfo("Asia/Riyadh")

CSV_FIELDS = [
    "day",
    "symbol",
    "name",
    "source",
    "source_ar",
    "status",
    "result_ar",
    "appeared_local",
    "opened_local",
    "exit_local",
    "appear_price",
    "entry",
    "stop",
    "tp1",
    "tp2",
    "exit",
    "last",
    "pnl_pct",
    "r_multiple",
    "mfe_pct",
    "mae_pct",
    "time_to_open_sec",
    "hold_sec",
    "appear_change_pct",
    "appear_rvol",
    "appear_dollar_volume",
    "appear_has_news",
    "appear_news_title",
    "appear_score",
    "appear_checks_ok",
    "appear_session_ar",
    "strategies",
]


def _now() -> float:
    return time.time()


def _day_str(ts: float | None = None) -> str:
    dt = datetime.fromtimestamp(ts or _now(), RIYADH)
    return dt.strftime("%Y-%m-%d")


def _local(ts: float | int | None) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(float(ts), RIYADH).strftime("%Y-%m-%d %H:%M")


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def snapshot_from_row(row: dict[str, Any], *, source: str) -> dict[str, Any]:
    """Freeze board fields at appear/open for later analysis."""
    first_ts = row.get("first_ts") or row.get("appeared_ts")
    try:
        first_ts = float(first_ts) if first_ts else None
    except Exception:
        first_ts = None
    last = float(row.get("last") or row.get("entry") or 0)
    checks = row.get("checklist") or []
    checks_ok = row.get("checks_ok")
    if checks_ok is None and isinstance(checks, list):
        checks_ok = sum(1 for c in checks if isinstance(c, dict) and c.get("ok"))
    return {
        "appeared_ts": int(first_ts) if first_ts else None,
        "appeared_local": _local(first_ts) if first_ts else (row.get("appeared_local") or ""),
        "appear_price": round(last, 4 if last < 1 else 2) if last else None,
        "appear_change_pct": row.get("change_pct"),
        "appear_rvol": row.get("rvol"),
        "appear_volume": row.get("volume"),
        "appear_avg_volume": row.get("avg_volume"),
        "appear_dollar_volume": row.get("dollar_volume"),
        "appear_dollar_volume_label": row.get("dollar_volume_label"),
        "appear_market_cap": row.get("market_cap"),
        "appear_market_cap_label": row.get("market_cap_label"),
        "appear_cap_bucket": row.get("cap_bucket"),
        "appear_float_shares": row.get("float_shares"),
        "appear_low_float": bool(row.get("low_float")),
        "appear_has_news": bool(row.get("has_news")),
        "appear_news_title": row.get("news_title") or row.get("news_title_ar") or "",
        "appear_news_impact": row.get("news_impact") or row.get("impact") or 0,
        "appear_catalyst_ar": row.get("catalyst_ar") or row.get("catalysts") or [],
        "appear_score": row.get("score"),
        "appear_checks_ok": checks_ok,
        "appear_checks_total": row.get("checks_total") or (len(checks) if isinstance(checks, list) else None),
        "appear_checklist": checks if isinstance(checks, list) else [],
        "appear_session_ar": row.get("session_ar") or "",
        "appear_phase": row.get("phase") or "",
        "appear_tier_key": row.get("tier_key") or "",
        "appear_name": row.get("name") or "",
        "source": source,
    }


def append_lifecycle_event(event: str, payload: dict[str, Any], day: str | None = None) -> None:
    """Append-only JSONL event log for the Riyadh day."""
    day = day or _day_str()
    LIFECYCLE_DIR.mkdir(parents=True, exist_ok=True)
    path = LIFECYCLE_DIR / f"{day}.jsonl"
    row = {
        "ts": int(_now()),
        "local": datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M:%S"),
        "event": event,
        **payload,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _board_index() -> dict[str, dict[str, Any]]:
    """symbol → {source, first_ts, row} from qannas + sniper boards/seen."""
    out: dict[str, dict[str, Any]] = {}

    q_seen = _load_json(QANNAS_SEEN, {})
    q_board = _load_json(QANNAS_BOARD, {})
    items = q_board.get("items") if isinstance(q_board, dict) else []
    if isinstance(items, list):
        for it in items:
            if not isinstance(it, dict):
                continue
            sym = str(it.get("symbol") or "").upper()
            if not sym:
                continue
            first = it.get("first_ts") or q_seen.get(sym)
            out[sym] = {"source": "qannas", "first_ts": first, "row": it}

    if isinstance(q_seen, dict):
        for sym, ts in q_seen.items():
            s = str(sym).upper()
            if s not in out:
                out[s] = {"source": "qannas", "first_ts": ts, "row": {"symbol": s, "first_ts": ts}}

    s_seen = _load_json(SNIPER_SEEN, {})
    s_board = _load_json(SNIPER_BOARD, {})
    if isinstance(s_board, dict):
        for sym, meta in s_board.items():
            if not isinstance(meta, dict):
                continue
            s = str(sym).upper()
            row = meta.get("row") if isinstance(meta.get("row"), dict) else {}
            row = dict(row)
            row.setdefault("symbol", s)
            row.setdefault("first_ts", meta.get("first_ts") or (s_seen.get(s) if isinstance(s_seen, dict) else None))
            # Prefer qannas if already present (primary scanner for ledger)
            if s not in out:
                out[s] = {"source": "sniper", "first_ts": row.get("first_ts"), "row": row}

    return out


def enrich_trade_appearance(trade: dict[str, Any], board: dict[str, dict[str, Any]] | None = None) -> bool:
    """Backfill appearance fields onto an existing trade. Returns True if changed."""
    if trade.get("appeared_ts") and trade.get("appear_price") is not None:
        return False
    board = board or _board_index()
    sym = str(trade.get("symbol") or "").upper()
    meta = board.get(sym)
    if not meta:
        # still stamp opened as appear fallback
        if not trade.get("appeared_ts") and trade.get("opened_ts"):
            trade["appeared_ts"] = int(trade["opened_ts"])
            trade["appeared_local"] = trade.get("opened_local") or _local(trade["opened_ts"])
            return True
        return False
    snap = snapshot_from_row(meta["row"], source=str(meta.get("source") or trade.get("source") or ""))
    changed = False
    for k, v in snap.items():
        if k == "source":
            continue
        if trade.get(k) in (None, "", [], 0) and v not in (None, "", []):
            trade[k] = v
            changed = True
    if not trade.get("name") and snap.get("appear_name"):
        trade["name"] = snap["appear_name"]
        changed = True
    if trade.get("appeared_ts") and trade.get("opened_ts") and trade.get("time_to_open_sec") is None:
        trade["time_to_open_sec"] = max(0, int(trade["opened_ts"]) - int(trade["appeared_ts"]))
        changed = True
    return changed


def append_price_trail(trade: dict[str, Any], px: float, *, min_gap_sec: int = 60) -> None:
    """Record sparse price trail while trade is open."""
    if px <= 0:
        return
    trail = trade.get("price_trail")
    if not isinstance(trail, list):
        trail = []
        trade["price_trail"] = trail
    now = int(_now())
    if trail:
        last = trail[-1]
        if now - int(last.get("ts") or 0) < min_gap_sec:
            # still update last point if moved a lot
            entry = float(trade.get("entry") or 0)
            prev = float(last.get("px") or 0)
            if prev > 0 and abs(px - prev) / prev < 0.005:
                return
            if now - int(last.get("ts") or 0) < 20:
                return
    entry = float(trade.get("entry") or 0)
    pnl = round((px - entry) / entry * 100.0, 3) if entry > 0 else None
    trail.append({"ts": now, "local": _local(now), "px": px, "pnl_pct": pnl})
    # Cap trail length
    if len(trail) > 240:
        trade["price_trail"] = trail[-240:]


def _trade_lifecycle_row(t: dict[str, Any]) -> dict[str, Any]:
    opened = int(t.get("opened_ts") or 0)
    exited = int(t.get("exit_ts") or 0)
    appeared = int(t.get("appeared_ts") or opened or 0)
    hold = None
    if opened and exited:
        hold = max(0, exited - opened)
    elif opened and t.get("status") == "open":
        hold = max(0, int(_now()) - opened)
    tto = t.get("time_to_open_sec")
    if tto is None and appeared and opened:
        tto = max(0, opened - appeared)
    return {
        "id": t.get("id"),
        "symbol": t.get("symbol"),
        "name": t.get("name") or t.get("appear_name") or "",
        "source": t.get("source"),
        "source_ar": t.get("source_ar"),
        "strategies": t.get("strategies") or [],
        "side": t.get("side") or "long",
        "status": t.get("status"),
        "result_ar": t.get("result_ar"),
        "appeared_ts": appeared or None,
        "appeared_local": t.get("appeared_local") or _local(appeared),
        "opened_ts": opened or None,
        "opened_local": t.get("opened_local") or _local(opened),
        "exit_ts": exited or None,
        "exit_local": t.get("exit_local") or (_local(exited) if exited else ""),
        "time_to_open_sec": tto,
        "hold_sec": hold,
        "appear_price": t.get("appear_price"),
        "appear_change_pct": t.get("appear_change_pct"),
        "appear_rvol": t.get("appear_rvol"),
        "appear_dollar_volume": t.get("appear_dollar_volume"),
        "appear_dollar_volume_label": t.get("appear_dollar_volume_label"),
        "appear_market_cap_label": t.get("appear_market_cap_label"),
        "appear_cap_bucket": t.get("appear_cap_bucket"),
        "appear_low_float": t.get("appear_low_float"),
        "appear_has_news": t.get("appear_has_news"),
        "appear_news_title": t.get("appear_news_title"),
        "appear_news_impact": t.get("appear_news_impact"),
        "appear_score": t.get("appear_score"),
        "appear_checks_ok": t.get("appear_checks_ok"),
        "appear_checks_total": t.get("appear_checks_total"),
        "appear_checklist": t.get("appear_checklist") or [],
        "appear_session_ar": t.get("appear_session_ar"),
        "appear_phase": t.get("appear_phase"),
        "entry": t.get("entry"),
        "stop": t.get("stop"),
        "tp1": t.get("tp1"),
        "tp2": t.get("tp2"),
        "last": t.get("last"),
        "exit": t.get("exit"),
        "pnl_pct": t.get("pnl_pct"),
        "r_multiple": t.get("r_multiple"),
        "mfe_pct": t.get("mfe_pct"),
        "mae_pct": t.get("mae_pct"),
        "price_trail": t.get("price_trail") or [],
    }


def build_day_report(day: str | None = None) -> dict[str, Any]:
    """Assemble full-day analysis object from ledger + boards."""
    day = day or _day_str()
    ledger = _load_json(LEDGER_PATH, {"trades": []})
    trades_all = list(ledger.get("trades") or [])
    board = _board_index()

    # Enrich + filter today's trades
    today_trades: list[dict[str, Any]] = []
    changed_ledger = False
    for t in trades_all:
        tid = str(t.get("id") or "")
        opened_local = str(t.get("opened_local") or "")
        is_today = day in tid or opened_local.startswith(day)
        if not is_today:
            # also by opened_ts
            ots = t.get("opened_ts")
            if ots and _day_str(float(ots)) == day:
                is_today = True
        if not is_today:
            continue
        if enrich_trade_appearance(t, board):
            changed_ledger = True
        today_trades.append(t)

    if changed_ledger:
        try:
            ledger["trades"] = trades_all
            ledger["updated_ts"] = int(_now())
            ledger["updated_local"] = datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M %Z")
            LEDGER_PATH.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    traded_syms = {str(t.get("symbol") or "").upper() for t in today_trades}
    appeared_only: list[dict[str, Any]] = []
    for sym, meta in board.items():
        first = meta.get("first_ts")
        try:
            first_f = float(first) if first else 0.0
        except Exception:
            first_f = 0.0
        if first_f and _day_str(first_f) != day:
            continue
        if not first_f:
            continue
        if sym in traded_syms:
            continue
        # only qannas for appeared_only primary list; include sniper tagged
        snap = snapshot_from_row(meta["row"], source=str(meta.get("source") or ""))
        appeared_only.append(
            {
                "symbol": sym,
                "name": snap.get("appear_name") or "",
                "source": meta.get("source"),
                "source_ar": "القناص" if meta.get("source") == "qannas" else "ماسح القنص",
                "status": "appeared_only",
                "result_ar": "ظهر ولم تُفتح صفقة",
                **{k: v for k, v in snap.items() if k != "source"},
            }
        )
    appeared_only.sort(key=lambda r: int(r.get("appeared_ts") or 0))

    rows = [_trade_lifecycle_row(t) for t in today_trades]
    rows.sort(key=lambda r: int(r.get("appeared_ts") or r.get("opened_ts") or 0))

    closed = [r for r in rows if r.get("status") and r.get("status") != "open"]
    open_rows = [r for r in rows if r.get("status") == "open"]
    decided = [r for r in closed if r.get("pnl_pct") is not None]
    wins = [r for r in decided if float(r.get("pnl_pct") or 0) > 0]
    losses = [r for r in decided if float(r.get("pnl_pct") or 0) < 0]
    holds = [int(r["hold_sec"]) for r in closed if r.get("hold_sec") is not None]
    ttos = [int(r["time_to_open_sec"]) for r in rows if r.get("time_to_open_sec") is not None]

    summary = {
        "appeared": len(rows) + len(appeared_only),
        "opened": len(rows),
        "open_now": len(open_rows),
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(100.0 * len(wins) / len(decided), 1) if decided else None,
        "avg_pnl_pct": round(sum(float(r.get("pnl_pct") or 0) for r in decided) / len(decided), 3) if decided else None,
        "avg_r": round(sum(float(r.get("r_multiple") or 0) for r in decided) / len(decided), 3) if decided else None,
        "avg_hold_sec": int(sum(holds) / len(holds)) if holds else None,
        "avg_time_to_open_sec": int(sum(ttos) / len(ttos)) if ttos else None,
        "appeared_only_n": len(appeared_only),
    }

    return {
        "day": day,
        "timezone": "Asia/Riyadh",
        "generated_ts": int(_now()),
        "generated_local": datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M:%S %Z"),
        "note_ar": (
            "سجل يومي كامل: ظهور السهم في القناص → فتح الصفقة الورقية → الإغلاق "
            "(هدف/وقف/انتهاء). للتحليل — ليس تنفيذ وساطة."
        ),
        "summary": summary,
        "trades": rows,
        "appeared_only": appeared_only,
        "urls": {
            "json": f"/day-performance.json",
            "csv": f"/day-performance.csv",
            "dated_json": f"/day-performance-{day}.json",
            "dated_csv": f"/day-performance-{day}.csv",
        },
    }


def _to_csv(report: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    w.writeheader()
    day = report.get("day")
    for t in list(report.get("trades") or []) + list(report.get("appeared_only") or []):
        row = {k: t.get(k) for k in CSV_FIELDS}
        row["day"] = day
        strats = t.get("strategies") or []
        if isinstance(strats, list):
            row["strategies"] = "|".join(str(s) for s in strats)
        w.writerow(row)
    return buf.getvalue()


def write_day_report(
    day: str | None = None,
    *,
    out_dirs: list[Path] | None = None,
) -> dict[str, Any]:
    """Write day JSON/CSV under data/ and publish copies to docs/pages."""
    report = build_day_report(day)
    day = str(report["day"])
    DAY_DIR.mkdir(parents=True, exist_ok=True)
    dated_json = DAY_DIR / f"{day}.json"
    dated_csv = DAY_DIR / f"{day}.csv"
    dated_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    dated_csv.write_text(_to_csv(report), encoding="utf-8")

    dirs = out_dirs or [ROOT / "docs", ROOT / "pages"]
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    csv_body = _to_csv(report)
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
        (d / "day-performance.json").write_text(payload, encoding="utf-8")
        (d / "day-performance.csv").write_text(csv_body, encoding="utf-8")
        (d / f"day-performance-{day}.json").write_text(payload, encoding="utf-8")
        (d / f"day-performance-{day}.csv").write_text(csv_body, encoding="utf-8")
    return report

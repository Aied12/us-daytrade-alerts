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

MAX_TRADES = 400
OPEN_MAX_SEC = 6 * 3600  # day-trade horizon
MIN_HOLD_SEC = 90        # don't close instantly on same-tick noise


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
        # Keep newest + still-open
        open_ones = [t for t in trades if t.get("status") == "open"]
        closed = [t for t in trades if t.get("status") != "open"]
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
) -> int:
    """Open paper trades for new long setups. Returns number newly opened."""
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
            if existing and existing.get("status") == "open":
                # Refresh live mark only
                last = float(row.get("last") or entry)
                existing["last"] = _px(last)
                existing["mfe_pct"] = max(float(existing.get("mfe_pct") or 0), _pct(entry, last))
                existing["mae_pct"] = min(float(existing.get("mae_pct") or 0), _pct(entry, last))
                continue
            if existing and existing.get("status") != "open":
                continue  # already closed today
            trade = {
                "id": tid,
                "symbol": sym,
                "source": source,
                "source_ar": {"opps": "خطط الدخول", "sniper": "ماسح القنص", "jamal": "استراتيجية جمال"}.get(source, source),
                "strategies": _strategies_of(row, source),
                "side": "long",
                "opened_ts": int(now),
                "opened_local": datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M"),
                "entry": entry,
                "stop": stop,
                "tp1": tp1,
                "tp2": tp2,
                "last": _px(float(row.get("last") or entry)),
                "status": "open",
                "exit": None,
                "exit_ts": None,
                "exit_local": None,
                "pnl_pct": None,
                "r_multiple": None,
                "mfe_pct": 0.0,
                "mae_pct": 0.0,
                "result_ar": "مفتوح",
            }
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


def mark_to_market(price_by_symbol: dict[str, float]) -> dict[str, Any]:
    """Update open trades; close on SL / TP / timeout. Returns fresh summary."""
    data = _load()
    trades: list[dict[str, Any]] = list(data.get("trades") or [])
    now = _now()
    for t in trades:
        if t.get("status") != "open":
            continue
        sym = str(t.get("symbol") or "").upper()
        entry = float(t.get("entry") or 0)
        stop = float(t.get("stop") or 0)
        tp1 = float(t.get("tp1") or 0)
        tp2 = float(t.get("tp2") or 0)
        last = float(price_by_symbol.get(sym) or t.get("last") or entry)
        if last <= 0 or entry <= 0:
            continue
        t["last"] = _px(last)
        pnl = _pct(entry, last)
        t["mfe_pct"] = max(float(t.get("mfe_pct") or 0), pnl)
        t["mae_pct"] = min(float(t.get("mae_pct") or 0), pnl)
        age = now - float(t.get("opened_ts") or now)
        if age < MIN_HOLD_SEC:
            continue

        def _close(status: str, exit_px: float, label: str) -> None:
            t["status"] = status
            t["exit"] = _px(exit_px)
            t["exit_ts"] = int(now)
            t["exit_local"] = datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M")
            t["pnl_pct"] = _pct(entry, exit_px)
            t["r_multiple"] = _r_multiple(entry, stop, exit_px)
            t["result_ar"] = label

        # Long exits — worst first so gap-through SL wins
        if stop > 0 and last <= stop:
            _close("loss_sl", last, "وقف خسارة")
        elif tp2 > 0 and last >= tp2:
            _close("win_tp2", last, "هدف 2 ✓")
        elif tp1 > 0 and last >= tp1:
            _close("win_tp1", last, "هدف 1 ✓")
        elif age >= OPEN_MAX_SEC:
            label = "انتهى الوقت +" if pnl >= 0 else "انتهى الوقت −"
            _close("expired", last, label)

    data["trades"] = trades
    data["summary"] = summarize(trades)
    _save(data)
    return data["summary"]


def summarize(trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    trades = list(trades if trades is not None else (_load().get("trades") or []))
    closed = [t for t in trades if t.get("status") and t.get("status") != "open"]
    open_n = sum(1 for t in trades if t.get("status") == "open")
    wins = [t for t in closed if str(t.get("status", "")).startswith("win") or float(t.get("pnl_pct") or 0) > 0]
    losses = [t for t in closed if t.get("status") == "loss_sl" or float(t.get("pnl_pct") or 0) < 0]
    # expired with pnl==0 counted neither
    decided = [t for t in closed if t.get("pnl_pct") is not None]
    win_n = sum(1 for t in decided if float(t.get("pnl_pct") or 0) > 0)
    loss_n = sum(1 for t in decided if float(t.get("pnl_pct") or 0) < 0)
    flat_n = sum(1 for t in decided if float(t.get("pnl_pct") or 0) == 0)
    avg_pnl = round(sum(float(t.get("pnl_pct") or 0) for t in decided) / len(decided), 3) if decided else 0.0
    avg_r = round(
        sum(float(t.get("r_multiple") or 0) for t in decided if t.get("r_multiple") is not None) / max(1, len(decided)),
        3,
    ) if decided else 0.0
    win_rate = round(100.0 * win_n / len(decided), 1) if decided else None

    by_source: dict[str, dict[str, Any]] = {}
    by_strategy: dict[str, dict[str, Any]] = {}

    def _acc(bucket: dict[str, Any], t: dict[str, Any]) -> None:
        bucket["n"] = int(bucket.get("n") or 0) + 1
        if t.get("status") == "open":
            bucket["open"] = int(bucket.get("open") or 0) + 1
            return
        bucket["closed"] = int(bucket.get("closed") or 0) + 1
        pnl = float(t.get("pnl_pct") or 0)
        bucket["pnl_sum"] = round(float(bucket.get("pnl_sum") or 0) + pnl, 3)
        if pnl > 0:
            bucket["wins"] = int(bucket.get("wins") or 0) + 1
        elif pnl < 0:
            bucket["losses"] = int(bucket.get("losses") or 0) + 1

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
            rows.append(
                {
                    "name": name,
                    "n": int(b.get("n") or 0),
                    "open": int(b.get("open") or 0),
                    "closed": closed_n,
                    "wins": wins_b,
                    "losses": losses_b,
                    "win_rate": round(100.0 * wins_b / decided_b, 1) if decided_b else None,
                    "avg_pnl_pct": round(float(b.get("pnl_sum") or 0) / closed_n, 3) if closed_n else None,
                }
            )
        rows.sort(key=lambda r: (r["win_rate"] is not None, r["win_rate"] or -1, r["n"]), reverse=True)
        return rows

    recent_closed = sorted(
        [t for t in closed if t.get("exit_ts")],
        key=lambda t: int(t.get("exit_ts") or 0),
        reverse=True,
    )[:15]
    recent_open = [t for t in trades if t.get("status") == "open"][-12:]

    return {
        "open": open_n,
        "closed": len(closed),
        "wins": win_n,
        "losses": loss_n,
        "flat": flat_n,
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
                "pnl_pct": t.get("pnl_pct"),
                "r_multiple": t.get("r_multiple"),
                "result_ar": t.get("result_ar"),
                "opened_local": t.get("opened_local"),
                "exit_local": t.get("exit_local"),
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
                "tp1": t.get("tp1"),
                "pnl_pct": _pct(float(t.get("entry") or 0), float(t.get("last") or 0)),
                "mfe_pct": t.get("mfe_pct"),
                "mae_pct": t.get("mae_pct"),
                "opened_local": t.get("opened_local"),
                "result_ar": "مفتوح",
            }
            for t in recent_open
        ],
        "note_ar": "متابعة ورقية تعليمية: نجاح/فشل حسب وصول السعر للهدف أو الوقف بعد ظهور الإشارة — ليست أرباح محفظة حقيقية.",
    }


def sync_from_boards(
    *,
    opportunities: list[dict[str, Any]] | None = None,
    sniper: list[dict[str, Any]] | None = None,
    jamal: list[dict[str, Any]] | None = None,
    price_by_symbol: dict[str, float] | None = None,
) -> dict[str, Any]:
    """One-shot: ingest new setups, mark-to-market, return dashboard payload."""
    prices = dict(price_by_symbol or {})
    for rows in (opportunities or [], sniper or [], jamal or []):
        for r in rows:
            sym = str(r.get("symbol") or "").upper()
            last = float(r.get("last") or 0)
            if sym and last > 0:
                prices[sym] = last
    ingest_candidates(opportunities=opportunities, sniper=sniper, jamal=jamal)
    mark_to_market(prices)
    data = _load()
    summary = data.get("summary") or summarize(data.get("trades") or [])
    return {
        "updated_ts": data.get("updated_ts"),
        "updated_local": data.get("updated_local"),
        "trades_n": len(data.get("trades") or []),
        **summary,
    }

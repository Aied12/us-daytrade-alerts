"""المضاربه — US Pre-Market ORB Retest Scalp (US_PM_ORB_RETEST_SCALP).

Watchlist only: SPY, AAPL, TSLA, META
5m bars + extended hours · Premarket range 04:00–09:29 ET
Trade window 09:35–11:30 ET · Breakout → Retest → Confirm · 1.5R
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from bot.cacheutil import cached_call

UA = {"User-Agent": "Mozilla/5.0 us-daytrade-alerts/mudaraba"}
ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "data" / "mudaraba_scan_config.json"
NY = ZoneInfo("America/New_York")

NOTE_AR = (
    "المضاربه · ORB إعادة اختبار ما قبل الافتتاح · "
    "SPY/AAPL/TSLA/META فقط · 5د · نافذة 09:35–11:30 نيويورك · 1.5R"
)

DEFAULT_WATCHLIST = ["SPY", "AAPL", "TSLA", "META"]
NAMES = {
    "SPY": "SPDR S&P 500",
    "AAPL": "Apple",
    "TSLA": "Tesla",
    "META": "Meta Platforms",
}

MIN_PM_RANGE_PCT = 0.0015
BREAKOUT_BUFFER_PCT = 0.0005
RETEST_TOLERANCE_PCT = 0.0015
MIN_BODY_TO_RANGE = 0.55
R_MULTIPLE = 1.5
STOP_BUF_LONG = 0.999
STOP_BUF_SHORT = 1.001

PM_START = dtime(4, 0)
PM_END = dtime(9, 29)
TRADE_START = dtime(9, 35)
TRADE_END = dtime(11, 30)
NO_NEW_AFTER = dtime(11, 15)


@dataclass
class Bar:
    ts: int
    dt: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def _px(n: float) -> float:
    n = float(n or 0)
    if n <= 0:
        return 0.0
    return round(n, 4 if n < 1 else 3 if n < 100 else 2)


def _load_config() -> dict[str, Any]:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def watchlist_symbols() -> list[str]:
    cfg = _load_config()
    wl = cfg.get("watchlist") or []
    out: list[str] = []
    for row in wl:
        if isinstance(row, dict):
            if row.get("enabled", True) is False:
                continue
            sym = str(row.get("symbol") or "").upper().strip()
        else:
            sym = str(row).upper().strip()
        if sym and sym not in out:
            out.append(sym)
    return out or list(DEFAULT_WATCHLIST)


def _cfg_float(cfg: dict[str, Any], *path: str, default: float) -> float:
    cur: Any = cfg
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
    try:
        return float(cur)
    except Exception:
        return default


def _fetch_5m_bars(symbol: str) -> list[Bar]:
    def _call():
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "1d", "interval": "5m", "includePrePost": "true"},
            headers=UA,
            timeout=20,
        )
        r.raise_for_status()
        return r.json()

    try:
        data = cached_call(f"yh:5m:{symbol}", _call, ttl=45)
    except Exception:
        try:
            data = _call()
        except Exception:
            return []

    try:
        res = ((data.get("chart") or {}).get("result") or [None])[0]
        if not res:
            return []
        ts_list = res.get("timestamp") or []
        q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
        opens = q.get("open") or []
        highs = q.get("high") or []
        lows = q.get("low") or []
        closes = q.get("close") or []
        vols = q.get("volume") or []
        bars: list[Bar] = []
        for i, ts in enumerate(ts_list):
            try:
                o = opens[i]
                h = highs[i]
                lo = lows[i]
                c = closes[i]
                if o is None or h is None or lo is None or c is None:
                    continue
                dt = datetime.fromtimestamp(int(ts), tz=NY)
                bars.append(
                    Bar(
                        ts=int(ts),
                        dt=dt,
                        open=float(o),
                        high=float(h),
                        low=float(lo),
                        close=float(c),
                        volume=float(vols[i] or 0) if i < len(vols) else 0.0,
                    )
                )
            except Exception:
                continue
        return bars
    except Exception:
        return []


def _in_window(t: dtime, start: dtime, end: dtime) -> bool:
    return start <= t <= end


def _bull_confirm(cur: Bar, prev: Bar | None, min_body: float) -> bool:
    body = abs(cur.close - cur.open)
    rng = max(cur.high - cur.low, 1e-9)
    strong = cur.close > cur.open and (body / rng) >= min_body and (
        prev is None or cur.close > prev.close
    )
    engulf = False
    if prev is not None:
        engulf = (
            prev.close < prev.open
            and cur.close > cur.open
            and cur.close >= prev.open
            and cur.open <= prev.close
        )
    return bool(engulf or strong)


def _bear_confirm(cur: Bar, prev: Bar | None, min_body: float) -> bool:
    body = abs(cur.close - cur.open)
    rng = max(cur.high - cur.low, 1e-9)
    strong = cur.close < cur.open and (body / rng) >= min_body and (
        prev is None or cur.close < prev.close
    )
    engulf = False
    if prev is not None:
        engulf = (
            prev.close > prev.open
            and cur.close < cur.open
            and cur.close <= prev.open
            and cur.open >= prev.close
        )
    return bool(engulf or strong)


def _analyze_symbol(symbol: str, *, now: datetime | None = None) -> dict[str, Any]:
    cfg = _load_config()
    min_range = _cfg_float(cfg, "filters", "min_pm_range_pct", default=MIN_PM_RANGE_PCT)
    brk_buf = _cfg_float(cfg, "filters", "breakout_buffer_pct", default=BREAKOUT_BUFFER_PCT)
    ret_tol = _cfg_float(cfg, "filters", "retest_tolerance_pct", default=RETEST_TOLERANCE_PCT)
    min_body = _cfg_float(
        cfg, "entry", "confirmation", "min_body_to_range", default=MIN_BODY_TO_RANGE
    )
    r_mult = _cfg_float(cfg, "risk", "r_multiple", default=R_MULTIPLE)
    stop_long_m = _cfg_float(cfg, "risk", "stop_buffer_long_mult", default=STOP_BUF_LONG)
    stop_short_m = _cfg_float(cfg, "risk", "stop_buffer_short_mult", default=STOP_BUF_SHORT)
    skip_both = bool((cfg.get("filters") or {}).get("skip_if_both_sides_break", True))

    now = (now or datetime.now(NY)).astimezone(NY)
    now_t = now.time()
    session_date = now.date()
    bars = [b for b in _fetch_5m_bars(symbol) if b.dt.date() == session_date]

    empty = {
        "symbol": symbol,
        "name": NAMES.get(symbol, symbol),
        "strategy": "المضاربه",
        "strategy_key": "mudaraba",
        "strategy_code": "US_PM_ORB_RETEST_SCALP",
        "last": None,
        "change_pct": None,
        "pmh": None,
        "pml": None,
        "pm_mid": None,
        "pm_range_pct": None,
        "pm_range_pct_display": None,
        "long_break_level": None,
        "short_break_level": None,
        "side": None,
        "stage": "no_data",
        "stage_ar": "لا بيانات شارت",
        "checklist": [],
        "checks_ok": 0,
        "checks_total": 4,
        "complete": False,
        "alert": None,
        "entry": None,
        "stop": None,
        "tp": None,
        "r_multiple": r_mult,
        "tv_url": f"https://www.tradingview.com/chart/?symbol={symbol}",
        "session_ar": "",
        "note_ar": "",
        "in_trade_window": False,
        "can_enter": False,
    }

    if not bars:
        if now_t < PM_START:
            empty.update(
                {
                    "stage": "waiting_pm",
                    "stage_ar": "قبل Premarket — بانتظار 04:00 ET",
                    "session_ar": "Overnight",
                    "note_ar": "لا شموع لجلسة اليوم بعد",
                }
            )
        return empty

    last_bar = bars[-1]
    last = last_bar.close
    # day % vs first available regular/prev reference: use first RTH open-ish via meta-less approx
    day_openish = next((b.open for b in bars if b.dt.time() >= dtime(9, 30)), bars[0].open)
    chg = ((last - day_openish) / day_openish * 100) if day_openish else 0.0

    pm_bars = [b for b in bars if _in_window(b.dt.time(), PM_START, PM_END)]
    if not pm_bars:
        empty.update(
            {
                "last": _px(last),
                "change_pct": round(chg, 2),
                "stage": "waiting_pm",
                "stage_ar": "بانتظار نطاق ما قبل الافتتاح",
                "session_ar": "Premarket",
                "note_ar": "لا شموع Premarket بعد (04:00–09:29 ET)",
            }
        )
        return empty

    pmh = max(b.high for b in pm_bars)
    pml = min(b.low for b in pm_bars)
    pm_mid = (pmh + pml) / 2.0
    pm_range_pct = (pmh - pml) / pm_mid if pm_mid > 0 else 0.0
    long_lvl = pmh * (1 + brk_buf)
    short_lvl = pml * (1 - brk_buf)

    range_ok = pm_range_pct >= min_range
    in_trade = _in_window(now_t, TRADE_START, TRADE_END)
    can_new = now_t <= NO_NEW_AFTER
    before_trade = now_t < TRADE_START
    after_flat = now_t > TRADE_END

    # Scan bars from trade start for breakout / retest / confirm
    trade_bars = [b for b in bars if b.dt.time() >= TRADE_START]
    long_break = False
    short_break = False
    long_break_i = -1
    short_break_i = -1
    for i, b in enumerate(trade_bars):
        if b.close > long_lvl and not long_break:
            long_break = True
            long_break_i = i
        if b.close < short_lvl and not short_break:
            short_break = True
            short_break_i = i

    both_broke = long_break and short_break
    skip_day = skip_both and both_broke

    long_retest = False
    short_retest = False
    long_retest_i = -1
    short_retest_i = -1
    if long_break and not skip_day:
        for i in range(long_break_i, len(trade_bars)):
            b = trade_bars[i]
            if b.low <= pmh * (1 + ret_tol):
                long_retest = True
                long_retest_i = i
                break
    if short_break and not skip_day:
        for i in range(short_break_i, len(trade_bars)):
            b = trade_bars[i]
            if b.high >= pml * (1 - ret_tol):
                short_retest = True
                short_retest_i = i
                break

    alert: str | None = None
    side: str | None = None
    entry = stop = tp = None
    confirm_bar: Bar | None = None

    if not skip_day and in_trade and can_new:
        # Prefer the side that broke first; evaluate confirm on latest completed logic
        candidates: list[tuple[str, int]] = []
        if long_break and long_retest:
            candidates.append(("long", long_retest_i))
        if short_break and short_retest:
            candidates.append(("short", short_retest_i))
        # scan from retest index forward for confirmation on any later bar (use last matching)
        for side_key, start_i in candidates:
            for i in range(max(start_i, 0), len(trade_bars)):
                cur = trade_bars[i]
                prev = trade_bars[i - 1] if i > 0 else (pm_bars[-1] if pm_bars else None)
                if side_key == "long":
                    if not _bull_confirm(cur, prev, min_body):
                        continue
                    if cur.close < pmh * (1 - ret_tol):
                        continue
                    e = cur.close
                    s = min(cur.low, pmh) * stop_long_m
                    risk = e - s
                    if risk <= 0:
                        continue
                    t = e + r_mult * risk
                    alert = "LONG_ORB_RETEST"
                    side = "long"
                    entry, stop, tp = e, s, t
                    confirm_bar = cur
                else:
                    if not _bear_confirm(cur, prev, min_body):
                        continue
                    if cur.close > pml * (1 + ret_tol):
                        continue
                    e = cur.close
                    s = max(cur.high, pml) * stop_short_m
                    risk = s - e
                    if risk <= 0:
                        continue
                    t = e - r_mult * risk
                    alert = "SHORT_ORB_RETEST"
                    side = "short"
                    entry, stop, tp = e, s, t
                    confirm_bar = cur

    # Stage machine for UI
    if after_flat:
        stage, stage_ar = "force_flat", "إغلاق إجباري 11:30 ET"
    elif skip_day:
        stage, stage_ar = "skip_both", "تخطي اليوم — اختراق الجانبين"
    elif not range_ok:
        stage, stage_ar = "range_thin", "نطاق Premarket ضيق (<0.15%)"
    elif before_trade:
        if now_t < PM_START:
            stage, stage_ar = "waiting_pm", "قبل Premarket"
        elif now_t <= PM_END:
            stage, stage_ar = "building_pm", "بناء نطاق Premarket"
        else:
            stage, stage_ar = "range_ready", "النطاق جاهز — بانتظار 09:35"
    elif alert:
        stage, stage_ar = (
            "alert_long" if side == "long" else "alert_short",
            "إشارة دخول Long" if side == "long" else "إشارة دخول Short",
        )
    elif long_break and long_retest:
        stage, stage_ar = "retest_long", "إعادة اختبار PMH — بانتظار تأكيد"
    elif short_break and short_retest:
        stage, stage_ar = "retest_short", "إعادة اختبار PML — بانتظار تأكيد"
    elif long_break:
        stage, stage_ar = "breakout_long", "اختراق أعلى PMH — بانتظار إعادة اختبار"
    elif short_break:
        stage, stage_ar = "breakout_short", "اختراق أسفل PML — بانتظار إعادة اختبار"
    elif in_trade:
        stage, stage_ar = "watching", "مراقبة اختراق النطاق"
    else:
        stage, stage_ar = "idle", "خارج نافذة التداول"

    checklist = [
        {"key": "range", "ar": "نطاق ≥0.15%", "ok": range_ok},
        {
            "key": "oneside",
            "ar": "اختراق اتجاه واحد",
            "ok": (long_break ^ short_break) if (long_break or short_break) else False,
        },
        {
            "key": "retest",
            "ar": "إعادة اختبار",
            "ok": bool((long_break and long_retest) or (short_break and short_retest)),
        },
        {"key": "confirm", "ar": "شمعة تأكيد", "ok": bool(alert)},
    ]
    checks_ok = sum(1 for c in checklist if c["ok"])
    complete = bool(alert)

    session_ar = (
        "نافذة المضاربة"
        if in_trade
        else ("Premarket" if _in_window(now_t, PM_START, PM_END) else "خارج النافذة")
    )

    note_parts = []
    if not range_ok:
        note_parts.append(f"PM range {pm_range_pct*100:.2f}% < 0.15%")
    if both_broke:
        note_parts.append("كسر PMH و PML في نفس اليوم")
    if in_trade and not can_new and not alert:
        note_parts.append("لا إدخالات جديدة بعد 11:15")
    if confirm_bar:
        note_parts.append(f"تأكيد {confirm_bar.dt.strftime('%H:%M')} ET")

    return {
        "symbol": symbol,
        "name": NAMES.get(symbol, symbol),
        "strategy": "المضاربه",
        "strategy_key": "mudaraba",
        "strategy_code": "US_PM_ORB_RETEST_SCALP",
        "last": _px(last),
        "change_pct": round(chg, 2),
        "pmh": _px(pmh),
        "pml": _px(pml),
        "pm_mid": _px(pm_mid),
        "pm_range_pct": round(pm_range_pct, 6),
        "pm_range_pct_display": f"{pm_range_pct*100:.2f}%",
        "long_break_level": _px(long_lvl),
        "short_break_level": _px(short_lvl),
        "long_break": long_break,
        "short_break": short_break,
        "long_retest": long_retest,
        "short_retest": short_retest,
        "side": side,
        "stage": stage,
        "stage_ar": stage_ar,
        "checklist": checklist,
        "checks_ok": checks_ok,
        "checks_total": 4,
        "complete": complete,
        "alert": alert,
        "entry": _px(entry) if entry else None,
        "stop": _px(stop) if stop else None,
        "tp": _px(tp) if tp else None,
        "tp1": _px(tp) if tp else None,
        "r_multiple": r_mult,
        "tv_url": f"https://www.tradingview.com/chart/?symbol={symbol}",
        "session_ar": session_ar,
        "note_ar": " · ".join(note_parts),
        "in_trade_window": in_trade,
        "can_enter": bool(complete and in_trade and can_new),
        "tier_ar": "إشارة" if complete else stage_ar,
    }


def build_mudaraba_scanner(*, limit: int = 4, now: datetime | None = None) -> list[dict[str, Any]]:
    syms = watchlist_symbols()[: max(1, int(limit or 4))]
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(_analyze_symbol, s, now=now): s for s in syms}
        for fut in as_completed(futs):
            try:
                rows.append(fut.result())
            except Exception as e:
                sym = futs[fut]
                rows.append(
                    {
                        "symbol": sym,
                        "name": NAMES.get(sym, sym),
                        "strategy": "المضاربه",
                        "strategy_key": "mudaraba",
                        "stage": "error",
                        "stage_ar": f"خطأ: {e}",
                        "checklist": [],
                        "checks_ok": 0,
                        "checks_total": 4,
                        "complete": False,
                        "tv_url": f"https://www.tradingview.com/chart/?symbol={sym}",
                    }
                )
    # alerts first, then by checks, then watchlist order
    order = {s: i for i, s in enumerate(syms)}

    def _key(r: dict[str, Any]) -> tuple:
        return (
            0 if r.get("complete") else 1,
            -int(r.get("checks_ok") or 0),
            order.get(str(r.get("symbol") or ""), 99),
        )

    rows.sort(key=_key)
    return rows


def mudaraba_rules_payload() -> dict[str, Any]:
    cfg = _load_config()
    return {
        "watchlist": watchlist_symbols(),
        "excluded": list((cfg.get("excluded") or ["NVDA", "AMD", "QQQ"])),
        "timeframe": "5m",
        "premarket": "04:00–09:29 ET",
        "trade_window": "09:35–11:30 ET",
        "min_pm_range_pct": _cfg_float(cfg, "filters", "min_pm_range_pct", default=MIN_PM_RANGE_PCT),
        "breakout_buffer_pct": _cfg_float(
            cfg, "filters", "breakout_buffer_pct", default=BREAKOUT_BUFFER_PCT
        ),
        "retest_tolerance_pct": _cfg_float(
            cfg, "filters", "retest_tolerance_pct", default=RETEST_TOLERANCE_PCT
        ),
        "r_multiple": _cfg_float(cfg, "risk", "r_multiple", default=R_MULTIPLE),
        "alerts": (cfg.get("scan_alerts") or {"long": "LONG_ORB_RETEST", "short": "SHORT_ORB_RETEST"}),
    }

"""Top gainers with live/extended-hours prices (not stale prior-session screeners)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

from bot.cacheutil import cached_call

NY = ZoneInfo("America/New_York")
UA = {"User-Agent": "Mozilla/5.0 us-daytrade-alerts"}


def _money(n: float) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B$"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M$"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K$"
    return f"{n:.0f}$"


def session_phase(now: datetime | None = None) -> str:
    now = now or datetime.now(NY)
    t = now.time()
    from datetime import time as dtime

    if dtime(4, 0) <= t < dtime(9, 30):
        return "pre"
    if dtime(9, 30) <= t < dtime(16, 0):
        return "regular"
    if dtime(16, 0) <= t < dtime(20, 0):
        return "post"
    return "closed"


def session_label_ar(phase: str | None = None) -> str:
    phase = phase or session_phase()
    return {
        "pre": "Premarket الآن (قبل الافتتاح)",
        "regular": "الجلسة الرسمية الآن",
        "post": "After-hours الآن",
        "closed": "السوق مغلق — آخر سعر متاح",
    }.get(phase, "الآن")


def _yahoo_live(symbol: str) -> dict[str, Any] | None:
    """Current price incl. pre/post from 1m chart bars."""
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "1d", "interval": "1m", "includePrePost": "true"},
            headers=UA,
            timeout=15,
        )
        r.raise_for_status()
        res = (r.json().get("chart") or {}).get("result") or [None]
        res = res[0]
        if not res:
            return None
        meta = res.get("meta") or {}
        closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
        live = next((float(c) for c in reversed(closes) if c is not None), None)
        rth = float(meta.get("regularMarketPrice") or 0)
        prev_close = float(meta.get("chartPreviousClose") or meta.get("previousClose") or 0)
        phase = session_phase()
        # In pre/post, % moves vs last regular close (regularMarketPrice).
        # In RTH, % moves vs prior day close.
        if phase in ("pre", "post") and rth > 0:
            ref = rth
        else:
            ref = prev_close if prev_close > 0 else rth
        if live is None or live <= 0:
            live = rth if rth > 0 else None
        if live is None or ref <= 0:
            return None
        chg = (live - ref) / ref * 100
        return {
            "symbol": symbol.upper(),
            "last": round(live, 2),
            "ref": round(ref, 2),
            "change_pct": round(chg, 2),
            "rth_close": round(rth, 2) if rth else None,
            "phase": phase,
            "name": (meta.get("shortName") or meta.get("longName") or "")[:40],
            "volume": float(meta.get("regularMarketVolume") or 0),
        }
    except Exception:
        return None


def _screener_symbols(scr_id: str, count: int = 25) -> list[str]:
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved",
            params={"scrIds": scr_id, "count": count, "formatted": "false"},
            headers=UA,
            timeout=20,
        )
        r.raise_for_status()
        quotes = ((r.json().get("finance") or {}).get("result") or [{}])[0].get("quotes") or []
        return [str(q.get("symbol") or "").upper() for q in quotes if q.get("symbol")]
    except Exception:
        return []


def fetch_day_gainers(min_price: float = 5.0, limit: int = 20) -> list[dict[str, Any]]:
    """Live-ranked gainers (pre/RTH/post aware), price >= min_price."""

    def _build() -> list[dict]:
        phase = session_phase()
        # Candidates: extended movers first (TV premarket / Finviz), then Yahoo screeners
        cand: list[str] = []
        meta: dict[str, dict] = {}
        try:
            from bot.movers_universe import fetch_extended_mover_symbols

            for row in fetch_extended_mover_symbols(limit=60):
                s = str(row.get("symbol") or "").upper()
                if not s:
                    continue
                if s not in cand:
                    cand.append(s)
                meta[s] = row
        except Exception:
            pass
        for scr in ("day_gainers", "most_actives", "small_cap_gainers"):
            for s in _screener_symbols(scr, 30):
                if s and s not in cand:
                    cand.append(s)
        for s in ("SPY", "QQQ", "NVDA", "TSLA", "AMD", "AAPL", "META", "PLTR", "BA"):
            if s not in cand:
                cand.append(s)
        cand = cand[:100]

        # Premarket: match Webull mid-list (<$5) — floor $0.25. RTH/post keep caller floor.
        price_floor = 0.25 if phase == "pre" else float(min_price)
        lives: list[dict] = []
        seen_live: set[str] = set()
        with ThreadPoolExecutor(max_workers=10) as pool:
            futs = {pool.submit(_yahoo_live, s): s for s in cand}
            for fut in as_completed(futs):
                row = fut.result()
                if not row:
                    continue
                sym = row["symbol"]
                extra = meta.get(sym) or {}
                chg = float(row["change_pct"] or 0)
                last = float(row["last"] or 0)
                ref = float(row.get("ref") or 0)
                vol = float(row.get("volume") or 0)
                # Yahoo often reports 0% pre while Webull/TV already show the move (RJF/H/PHIO)
                src_chg = float(extra.get("tv_premarket_change") or extra.get("screener_chg") or 0)
                src_px = float(extra.get("tv_premarket_price") or extra.get("screener_price") or 0)
                src_vol = float(extra.get("volume") or 0)
                if phase == "pre" and src_chg > 0 and (chg <= 0.5 or (src_chg - chg) >= 5.0):
                    chg = src_chg
                    if src_px > 0:
                        last = src_px
                    # Webull often stubs volume as 1 for large names — keep Yahoo prints
                    if src_vol > vol and src_vol >= 100:
                        vol = src_vol
                    pre_close = float(extra.get("pre_close") or 0)
                    if pre_close > 0:
                        ref = pre_close
                if last < price_floor:
                    continue
                if chg <= 0:
                    continue
                dollar = last * vol
                lives.append(
                    {
                        "symbol": sym,
                        "name": row.get("name") or extra.get("name") or "",
                        "last": round(last, 4 if last < 1 else 2),
                        "change_pct": round(chg, 2),
                        "ref": round(ref, 4 if ref < 1 else 2) if ref else row.get("ref"),
                        "rth_close": row.get("rth_close"),
                        "volume": vol,
                        "dollar_volume": round(dollar, 2),
                        "dollar_volume_label": _money(dollar),
                        "market_cap": float(extra.get("market_cap") or 0) or None,
                        "phase": row["phase"],
                        "session_ar": session_label_ar(row["phase"]),
                        "tv_url": f"https://www.tradingview.com/chart/?symbol={sym}",
                        "source": extra.get("source") or "yahoo",
                    }
                )
                seen_live.add(sym)
        # Webull-only rows when Yahoo chart fails entirely
        if phase == "pre":
            for sym, extra in meta.items():
                if sym in seen_live:
                    continue
                if str(extra.get("source") or "") != "webull_pre":
                    continue
                chg = float(extra.get("tv_premarket_change") or 0)
                last = float(extra.get("tv_premarket_price") or 0)
                if chg <= 0 or last < price_floor:
                    continue
                vol = float(extra.get("volume") or 0)
                dollar = last * vol
                pre_close = float(extra.get("pre_close") or 0)
                lives.append(
                    {
                        "symbol": sym,
                        "name": extra.get("name") or "",
                        "last": round(last, 4 if last < 1 else 2),
                        "change_pct": round(chg, 2),
                        "ref": round(pre_close, 4 if pre_close < 1 else 2) if pre_close else None,
                        "rth_close": pre_close or None,
                        "volume": vol,
                        "dollar_volume": round(dollar, 2),
                        "dollar_volume_label": _money(dollar),
                        "market_cap": float(extra.get("market_cap") or 0) or None,
                        "phase": phase,
                        "session_ar": session_label_ar(phase),
                        "tv_url": f"https://www.tradingview.com/chart/?symbol={sym}",
                        "source": "webull_pre",
                    }
                )
        from bot.flow_filter import gainer_passes_flow

        lives = [x for x in lives if gainer_passes_flow(x, phase)]
        lives.sort(key=lambda x: x["change_pct"], reverse=True)
        return lives[:limit]

    cache_key = f"live_gainers:{min_price}:{limit}:{session_phase()}"
    try:
        rows = cached_call(cache_key, _build, ttl=60)
        return list(rows or [])
    except Exception:
        try:
            return _build()
        except Exception:
            return []


def watchlist_gainers(snaps, min_price: float = 5.0, limit: int = 15) -> list[dict[str, Any]]:
    """Fallback from watchlist snapshots (already filtered elsewhere)."""
    rows = []
    phase = session_phase()
    for s in snaps or []:
        if s.last < min_price or s.change_pct <= 0:
            continue
        dollar = s.last * s.volume
        rows.append(
            {
                "symbol": s.symbol,
                "name": "",
                "last": round(s.last, 2),
                "change_pct": round(s.change_pct, 2),
                "volume": float(s.volume),
                "dollar_volume": round(dollar, 2),
                "dollar_volume_label": _money(dollar),
                "phase": phase,
                "session_ar": session_label_ar(phase),
                "tv_url": f"https://www.tradingview.com/chart/?symbol={s.symbol}",
            }
        )
    rows = [x for x in rows if float(x.get("dollar_volume") or 0) >= 5_000_000]
    rows.sort(key=lambda x: x["change_pct"], reverse=True)
    return rows[:limit]

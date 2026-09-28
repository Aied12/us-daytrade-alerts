"""Broad US equity movers universe (beyond Yahoo predefined screeners).

Primary: TradingView america scanner (premarket_change / change).
Fallbacks: Yahoo crumb % screener, Finviz top-gainers via Jina.
Used so names like CLRO (+80% pre) still enter gainers/القناص.
"""

from __future__ import annotations

import re
from typing import Any

import requests

from bot.cacheutil import cached_call
from bot.gainers import session_phase

UA = {
    "User-Agent": "Mozilla/5.0 (compatible; us-daytrade-alerts/1.0)",
    "Content-Type": "application/json",
    "Accept": "application/json,text/plain,*/*",
}

TV_URL = "https://scanner.tradingview.com/america/scan"
EXCHANGES = ["AMEX", "NASDAQ", "NYSE"]


def _tv_scan(
    *,
    sort_by: str,
    filter_left: str,
    min_chg: float,
    limit: int = 50,
) -> list[dict[str, Any]]:
    body = {
        "filter": [
            {"left": "type", "operation": "equal", "right": "stock"},
            {"left": "exchange", "operation": "in_range", "right": EXCHANGES},
            {"left": filter_left, "operation": "greater", "right": float(min_chg)},
            {"left": "active_symbol", "operation": "equal", "right": True},
        ],
        "options": {"lang": "en"},
        "markets": ["america"],
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": [
            "name",
            "close",
            "change",
            "premarket_change",
            "premarket_close",
            "volume",
            "market_cap_basic",
            "description",
        ],
        "sort": {"sortBy": sort_by, "sortOrder": "desc"},
        "range": [0, int(limit)],
    }
    r = requests.post(TV_URL, json=body, headers=UA, timeout=25)
    if not r.ok:
        return []
    rows: list[dict[str, Any]] = []
    for item in r.json().get("data") or []:
        raw = item.get("s") or ""
        sym = str(raw).split(":")[-1].upper()
        if not sym or not sym.isalpha() or len(sym) > 5:
            continue
        d = item.get("d") or []
        # columns: name, close, change, premarket_change, premarket_close, volume, market_cap, description
        close = float(d[1] or 0) if len(d) > 1 else 0.0
        chg = float(d[2] or 0) if len(d) > 2 else 0.0
        pre_chg = float(d[3] or 0) if len(d) > 3 and d[3] is not None else None
        pre_px = float(d[4] or 0) if len(d) > 4 and d[4] is not None else None
        vol = float(d[5] or 0) if len(d) > 5 else 0.0
        mcap = float(d[6] or 0) if len(d) > 6 else 0.0
        name = str(d[7] or d[0] or "") if len(d) > 0 else ""
        rows.append(
            {
                "symbol": sym,
                "name": name[:48],
                "tv_close": close,
                "tv_change": chg,
                "tv_premarket_change": pre_chg,
                "tv_premarket_price": pre_px,
                "volume": vol,
                "market_cap": mcap,
                "source": f"tv:{filter_left}",
            }
        )
    return rows


def fetch_tradingview_movers(*, limit: int = 50, min_chg: float = 15.0) -> list[dict[str, Any]]:
    """Session-aware movers: premarket_change in pre, else day change."""

    def _call() -> list[dict[str, Any]]:
        phase = session_phase()
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        scans: list[tuple[str, str, float]] = []
        if phase == "pre":
            scans.append(("premarket_change", "premarket_change", max(10.0, min_chg * 0.7)))
            scans.append(("change", "change", min_chg))
        elif phase == "post":
            scans.append(("change", "change", min_chg))
        else:
            scans.append(("change", "change", min_chg))
        for sort_by, filt, thr in scans:
            try:
                for row in _tv_scan(sort_by=sort_by, filter_left=filt, min_chg=thr, limit=limit):
                    sym = row["symbol"]
                    if sym in seen:
                        continue
                    seen.add(sym)
                    out.append(row)
                    if len(out) >= limit:
                        return out
            except Exception:
                continue
        return out

    return cached_call(f"tv_movers:{session_phase()}:{limit}:{min_chg}", _call, ttl=45) or []


def fetch_yahoo_pct_movers(*, limit: int = 40, min_chg: float = 15.0) -> list[dict[str, Any]]:
    """Yahoo custom screener (needs crumb). RTH % — still useful candidate seed."""

    def _call() -> list[dict[str, Any]]:
        s = requests.Session()
        s.headers.update({"User-Agent": UA["User-Agent"]})
        try:
            s.get("https://fc.yahoo.com", timeout=12)
            crumb = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=12).text.strip()
        except Exception:
            return []
        if not crumb or "html" in crumb.lower():
            return []
        body = {
            "size": limit,
            "offset": 0,
            "sortField": "percentchange",
            "sortType": "DESC",
            "quoteType": "EQUITY",
            "query": {
                "operator": "AND",
                "operands": [
                    {"operator": "eq", "operands": ["region", "us"]},
                    {"operator": "gte", "operands": ["percentchange", float(min_chg)]},
                    {"operator": "gte", "operands": ["intradayprice", 0.25]},
                    {"operator": "lte", "operands": ["intradayprice", 150]},
                ],
            },
        }
        try:
            r = s.post(
                f"https://query1.finance.yahoo.com/v1/finance/screener?crumb={crumb}",
                json=body,
                timeout=25,
            )
            if not r.ok:
                return []
            quotes = ((r.json().get("finance") or {}).get("result") or [{}])[0].get("quotes") or []
        except Exception:
            return []
        rows: list[dict[str, Any]] = []
        for q in quotes:
            sym = str(q.get("symbol") or "").upper()
            if not sym.isalpha() or len(sym) > 5:
                continue
            rows.append(
                {
                    "symbol": sym,
                    "name": str(q.get("shortName") or q.get("longName") or "")[:48],
                    "market_cap": float(q.get("marketCap") or 0),
                    "screener_chg": float(q.get("regularMarketChangePercent") or 0),
                    "screener_price": float(q.get("regularMarketPrice") or 0),
                    "float_shares": float(q.get("floatShares") or q.get("sharesOutstanding") or 0),
                    "source": "yahoo_pct",
                }
            )
        return rows

    return cached_call(f"yahoo_pct:{limit}:{min_chg}", _call, ttl=60) or []


def fetch_finviz_top_gainers(*, limit: int = 30) -> list[dict[str, Any]]:
    """Finviz top-gainers via Jina (site often blocks direct bots)."""

    def _call() -> list[dict[str, Any]]:
        try:
            r = requests.get(
                "https://r.jina.ai/https://finviz.com/screener.ashx?v=111&s=ta_topgainers",
                headers={"User-Agent": UA["User-Agent"], "Accept": "text/plain"},
                timeout=40,
            )
            if not r.ok:
                return []
            syms = re.findall(r"finviz\.com/stock\?t=([A-Z]{1,5})", r.text)
            out: list[dict[str, Any]] = []
            seen: set[str] = set()
            for sym in syms:
                if sym in seen or not sym.isalpha():
                    continue
                seen.add(sym)
                out.append({"symbol": sym, "source": "finviz"})
                if len(out) >= limit:
                    break
            return out
        except Exception:
            return []

    return cached_call(f"finviz_gainers:{limit}", _call, ttl=90) or []


def fetch_extended_mover_symbols(*, limit: int = 80) -> list[dict[str, Any]]:
    """Merge TV + Yahoo % + Finviz into a deduped candidate list with metadata."""
    by: dict[str, dict[str, Any]] = {}
    phase = session_phase()
    # Order matters for merge hints: TV first (session-aware), then Finviz, then Yahoo RTH %
    for row in (
        list(fetch_tradingview_movers(limit=50, min_chg=12.0))
        + list(fetch_finviz_top_gainers(limit=25))
        + list(fetch_yahoo_pct_movers(limit=40, min_chg=12.0))
    ):
        sym = str(row.get("symbol") or "").upper()
        if not sym:
            continue
        prev = by.get(sym) or {}
        by[sym] = {**prev, **{k: v for k, v in row.items() if v not in (None, "", 0, 0.0)}}
        by[sym]["symbol"] = sym
    rows = list(by.values())

    def _score(r: dict[str, Any]) -> float:
        pre = float(r.get("tv_premarket_change") or 0)
        day = float(r.get("tv_change") or 0)
        yh = float(r.get("screener_chg") or 0)
        src = str(r.get("source") or "")
        # Premarket: prefer TV premarket_change; boost Finviz/TV over Yahoo RTH junk
        if phase == "pre":
            if pre > 0:
                return pre + 2000.0
            if src.startswith("tv:") or src == "finviz":
                return max(day, 25.0) + 1000.0
            return max(day, min(yh, 60.0))
        if src.startswith("tv:") or src == "finviz":
            return max(day, pre, 20.0) + 500.0
        return max(day, pre, min(yh, 120.0))

    rows.sort(key=_score, reverse=True)
    return rows[:limit]

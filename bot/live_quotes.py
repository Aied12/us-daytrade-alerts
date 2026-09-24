"""Live last prices: Finnhub/Alpaca in RTH; Yahoo 1m+prepost in extended hours."""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, time as dtime
from typing import Iterable
from zoneinfo import ZoneInfo

import requests

from bot.cacheutil import cached_call

NY = ZoneInfo("America/New_York")
UA = {"User-Agent": "Mozilla/5.0 us-daytrade-alerts"}


@dataclass
class LiveQuote:
    symbol: str
    last: float
    prev_close: float
    change_pct: float
    source: str
    asof_ts: float
    phase: str = "regular"
    volume: float = 0.0


def _finnhub_key() -> str:
    return os.getenv("FINNHUB_API_KEY", "").strip()


def _alpaca_keys() -> tuple[str, str]:
    return (
        os.getenv("ALPACA_API_KEY", "").strip(),
        os.getenv("ALPACA_API_SECRET", "").strip(),
    )


def session_phase(now: datetime | None = None) -> str:
    now = now or datetime.now(NY)
    t = now.time()
    if dtime(4, 0) <= t < dtime(9, 30):
        return "pre"
    if dtime(9, 30) <= t < dtime(16, 0):
        return "regular"
    if dtime(16, 0) <= t < dtime(20, 0):
        return "post"
    return "closed"


def quote_provider() -> str:
    phase = session_phase()
    if phase in ("pre", "post", "closed"):
        return "yahoo_ext"
    if _finnhub_key():
        return "finnhub"
    k, s = _alpaca_keys()
    if k and s:
        return "alpaca"
    return "yahoo_ext"


def price_lag_label_ar() -> str:
    phase = session_phase()
    if phase == "pre":
        return "Premarket الآن (Yahoo لحظي ممتد)"
    if phase == "post":
        return "After-hours الآن (Yahoo لحظي ممتد)"
    if phase == "closed":
        return "السوق مغلق — آخر سعر متاح"
    p = quote_provider()
    if p == "finnhub":
        return "أسعار شبه لحظية (Finnhub)"
    if p == "alpaca":
        return "أسعار شبه لحظية (Alpaca IEX)"
    return "أسعار Yahoo اللحظية"


def _yahoo_ext_one(symbol: str) -> LiveQuote | None:
    """Current price including pre/post from 1-minute bars."""

    def _call():
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "1d", "interval": "1m", "includePrePost": "true"},
            headers=UA,
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    try:
        data = cached_call(f"yh:ext:{symbol}", _call, ttl=25)
    except Exception:
        try:
            data = _call()
        except Exception:
            return None
    try:
        res = ((data.get("chart") or {}).get("result") or [None])[0]
        if not res:
            return None
        meta = res.get("meta") or {}
        closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
        live = next((float(c) for c in reversed(closes) if c is not None), None)
        rth = float(meta.get("regularMarketPrice") or 0)
        prev_close = float(meta.get("chartPreviousClose") or meta.get("previousClose") or 0)
        phase = session_phase()
        # Premarket/post % vs last regular close
        if phase in ("pre", "post") and rth > 0:
            ref = rth
        else:
            ref = prev_close if prev_close > 0 else rth
        if live is None or live <= 0:
            live = rth if rth > 0 else None
        if live is None or ref <= 0:
            return None
        chg = (live - ref) / ref * 100
        volume = float(meta.get("regularMarketVolume") or 0)
        return LiveQuote(
            symbol=symbol.upper(),
            last=round(live, 4),
            prev_close=round(ref, 4),
            change_pct=round(chg, 4),
            source="yahoo_ext",
            asof_ts=time.time(),
            phase=phase,
            volume=volume,
        )
    except Exception:
        return None


def _fetch_finnhub_one(symbol: str) -> LiveQuote | None:
    key = _finnhub_key()
    if not key:
        return None

    def _call():
        r = requests.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": symbol, "token": key},
            timeout=12,
        )
        r.raise_for_status()
        return r.json()

    try:
        data = cached_call(f"fh:quote:{symbol}", _call, ttl=20)
    except Exception:
        return None
    last = float(data.get("c") or 0)
    prev = float(data.get("pc") or 0)
    if last <= 0:
        return None
    chg = ((last - prev) / prev * 100) if prev else float(data.get("dp") or 0)
    return LiveQuote(
        symbol=symbol.upper(),
        last=last,
        prev_close=prev or last,
        change_pct=round(chg, 4),
        source="finnhub",
        asof_ts=float(data.get("t") or time.time()),
        phase=session_phase(),
    )


def _fetch_alpaca_batch(symbols: list[str]) -> dict[str, LiveQuote]:
    key, secret = _alpaca_keys()
    if not key or not secret or not symbols:
        return {}
    url = "https://data.alpaca.markets/v2/stocks/snapshots"
    try:
        r = requests.get(
            url,
            params={"symbols": ",".join(symbols), "feed": "iex"},
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            timeout=15,
        )
        r.raise_for_status()
        payload = r.json() or {}
    except Exception:
        return {}
    out: dict[str, LiveQuote] = {}
    for sym, snap in payload.items():
        try:
            trade = (snap or {}).get("latestTrade") or {}
            prev = (snap or {}).get("prevDailyBar") or {}
            day = (snap or {}).get("dailyBar") or {}
            last = float(trade.get("p") or day.get("c") or 0)
            prev_close = float(prev.get("c") or 0)
            if last <= 0:
                continue
            chg = ((last - prev_close) / prev_close * 100) if prev_close else 0.0
            volume = float(day.get("v") or 0)
            out[sym.upper()] = LiveQuote(
                symbol=sym.upper(),
                last=last,
                prev_close=prev_close or last,
                change_pct=round(chg, 4),
                source="alpaca",
                asof_ts=time.time(),
                phase=session_phase(),
                volume=volume,
            )
        except Exception:
            continue
    return out


def fetch_live_quotes(symbols: Iterable[str]) -> dict[str, LiveQuote]:
    syms = [s.upper().strip() for s in symbols if s]
    if not syms:
        return {}
    phase = session_phase()
    out: dict[str, LiveQuote] = {}

    # Extended hours: Yahoo pre/post is the truth (Finnhub free stays on RTH close)
    if phase in ("pre", "post", "closed"):
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {pool.submit(_yahoo_ext_one, s): s for s in syms}
            for fut in as_completed(futs):
                q = fut.result()
                if q:
                    out[q.symbol] = q
        return out

    # Regular session: Finnhub/Alpaca first, Yahoo fill gaps
    provider = "finnhub" if _finnhub_key() else ("alpaca" if all(_alpaca_keys()) else "yahoo_ext")
    if provider == "alpaca":
        for i in range(0, len(syms), 40):
            out.update(_fetch_alpaca_batch(syms[i : i + 40]))
    elif provider == "finnhub":
        for sym in syms:
            q = _fetch_finnhub_one(sym)
            if q:
                out[sym] = q
            time.sleep(0.03)
    missing = [s for s in syms if s not in out]
    if missing:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = {pool.submit(_yahoo_ext_one, s): s for s in missing}
            for fut in as_completed(futs):
                q = fut.result()
                if q:
                    out[q.symbol] = q
    return out


def apply_live_quote(snap, live: LiveQuote | None):
    """Mutate QuoteSnapshot last / change_pct / prev_close when live exists."""
    if snap is None or live is None or live.last <= 0:
        return snap
    snap.last = float(live.last)
    if live.prev_close > 0:
        snap.prev_close = float(live.prev_close)
        snap.change_pct = ((live.last - live.prev_close) / live.prev_close) * 100
    else:
        snap.change_pct = float(live.change_pct)
    return snap

"""Near-real-time last prices (Finnhub / Alpaca). Yahoo alone is ~15m delayed."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Iterable

import requests

from bot.cacheutil import cached_call


@dataclass
class LiveQuote:
    symbol: str
    last: float
    prev_close: float
    change_pct: float
    source: str
    asof_ts: float


def _finnhub_key() -> str:
    return os.getenv("FINNHUB_API_KEY", "").strip()


def _alpaca_keys() -> tuple[str, str]:
    return (
        os.getenv("ALPACA_API_KEY", "").strip(),
        os.getenv("ALPACA_API_SECRET", "").strip(),
    )


def quote_provider() -> str:
    if _finnhub_key():
        return "finnhub"
    k, s = _alpaca_keys()
    if k and s:
        return "alpaca"
    return "yahoo"


def price_lag_label_ar() -> str:
    p = quote_provider()
    if p == "finnhub":
        return "أسعار شبه لحظية (Finnhub)"
    if p == "alpaca":
        return "أسعار شبه لحظية (Alpaca IEX)"
    return "تأخير Yahoo ≈ 15 دقيقة"


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
    )


def _fetch_alpaca_batch(symbols: list[str]) -> dict[str, LiveQuote]:
    key, secret = _alpaca_keys()
    if not key or not secret or not symbols:
        return {}
    # Free IEX feed
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
            out[sym.upper()] = LiveQuote(
                symbol=sym.upper(),
                last=last,
                prev_close=prev_close or last,
                change_pct=round(chg, 4),
                source="alpaca",
                asof_ts=time.time(),
            )
        except Exception:
            continue
    return out


def fetch_live_quotes(symbols: Iterable[str]) -> dict[str, LiveQuote]:
    syms = [s.upper().strip() for s in symbols if s]
    if not syms:
        return {}
    provider = quote_provider()
    if provider == "alpaca":
        # Alpaca allows batch; chunk to stay safe
        out: dict[str, LiveQuote] = {}
        for i in range(0, len(syms), 40):
            out.update(_fetch_alpaca_batch(syms[i : i + 40]))
        return out
    if provider == "finnhub":
        out = {}
        for sym in syms:
            q = _fetch_finnhub_one(sym)
            if q:
                out[sym] = q
            time.sleep(0.05)  # soft pacing for free tier
        return out
    return {}


def apply_live_quote(snap, live: LiveQuote | None):
    """Mutate QuoteSnapshot last / change_pct / prev_close when live exists."""
    if snap is None or live is None or live.last <= 0:
        return snap
    snap.last = live.last
    if live.prev_close > 0:
        snap.prev_close = live.prev_close
        snap.change_pct = ((live.last - live.prev_close) / live.prev_close) * 100
    else:
        snap.change_pct = live.change_pct
    return snap

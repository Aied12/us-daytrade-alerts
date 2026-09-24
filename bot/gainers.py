"""Top day-gainers table (min price filter, e.g. $5+)."""

from __future__ import annotations

from typing import Any

import requests

from bot.cacheutil import cached_call


def fetch_day_gainers(min_price: float = 5.0, limit: int = 20) -> list[dict[str, Any]]:
    """Yahoo predefined day_gainers screener — price >= min_price."""

    def _download() -> list[dict]:
        r = requests.get(
            "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved",
            params={"scrIds": "day_gainers", "count": 50, "formatted": "false"},
            headers={"User-Agent": "Mozilla/5.0 us-daytrade-alerts"},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        quotes = ((data.get("finance") or {}).get("result") or [{}])[0].get("quotes") or []
        out: list[dict] = []
        for q in quotes:
            try:
                last = float(q.get("regularMarketPrice") or 0)
                chg = float(q.get("regularMarketChangePercent") or 0)
                vol = float(q.get("regularMarketVolume") or 0)
                name = (q.get("shortName") or q.get("longName") or "").strip()
                sym = (q.get("symbol") or "").upper()
                if not sym or last < min_price or chg <= 0:
                    continue
                dollar = last * vol
                out.append(
                    {
                        "symbol": sym,
                        "name": name[:40],
                        "last": round(last, 2),
                        "change_pct": round(chg, 2),
                        "volume": vol,
                        "dollar_volume": round(dollar, 2),
                        "dollar_volume_label": _money(dollar),
                        "tv_url": f"https://www.tradingview.com/chart/?symbol={sym}",
                    }
                )
            except Exception:
                continue
        out.sort(key=lambda x: x["change_pct"], reverse=True)
        return out[:limit]

    try:
        rows = cached_call(f"yh:day_gainers:{min_price}:{limit}", _download, ttl=90)
        return list(rows or [])
    except Exception:
        try:
            return _download()
        except Exception:
            return []


def _money(n: float) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B$"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M$"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K$"
    return f"{n:.0f}$"


def watchlist_gainers(snaps, min_price: float = 5.0, limit: int = 15) -> list[dict[str, Any]]:
    """Fallback: gainers from current watchlist snapshots."""
    rows = []
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
                "tv_url": f"https://www.tradingview.com/chart/?symbol={s.symbol}",
            }
        )
    rows.sort(key=lambda x: x["change_pct"], reverse=True)
    return rows[:limit]

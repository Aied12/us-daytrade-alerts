"""Sniper scanner: our own cheap-runner board (long-only day trade).

Independent of any third-party X/Twitter tip style. Rules:
- low price band ($0.30–$8)
- strong day % move
- news/catalyst preferred when available
- separate from the conservative liquid large-cap board
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from bot.catalyst_scan import enrich_news_item, impact_score_1_5
from bot.gainers import _money, session_label_ar, session_phase, _screener_symbols
from bot.cacheutil import cached_call

UA = {"User-Agent": "Mozilla/5.0"}
ROOT = Path(__file__).resolve().parent.parent
SEEN_PATH = ROOT / "data" / "sniper_seen.json"
RIYADH = ZoneInfo("Asia/Riyadh")
NY = ZoneInfo("America/New_York")

# Price band: cheap / near-penny to low single-digit (not APA-style mid/large)
SNIPER_MIN_PRICE = 0.30
SNIPER_MAX_PRICE = 8.00
# Strong move floors (day-trade runners) — vs prior close (يوم كامل)
SNIPER_MIN_CHG = 8.0
SNIPER_MIN_CHG_WITH_NEWS = 5.0
SNIPER_MIN_CHG_PRE = 6.0
# Participation — softer than main board, but not empty prints
SNIPER_MIN_DOLLAR = 400_000
SNIPER_MIN_DOLLAR_HOT = 200_000  # if move is huge
# Drop first-seen entries older than this — next session starts clean
SEEN_TTL_SEC = 20 * 3600


def _load_seen() -> dict[str, float]:
    try:
        data = json.loads(SEEN_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        now = datetime.now(timezone.utc).timestamp()
        return {
            str(k).upper(): float(v)
            for k, v in data.items()
            if float(v) > now - SEEN_TTL_SEC
        }
    except Exception:
        return {}


def _save_seen(seen: dict[str, float]) -> None:
    try:
        SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        items = sorted(seen.items(), key=lambda kv: kv[1], reverse=True)[:200]
        SEEN_PATH.write_text(
            json.dumps(dict(items), ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def _stamp_appeared(ts: float) -> dict[str, Any]:
    """Clock + relative Arabic label for first appearance on sniper board."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    local = dt.astimezone(RIYADH)
    ny = dt.astimezone(NY)
    now = datetime.now(timezone.utc).timestamp()
    sec = max(0, int(now - ts))
    if sec < 45:
        ago = "الآن"
    elif sec < 90:
        ago = "منذ دقيقة"
    elif sec < 3600:
        ago = f"منذ {sec // 60} دقيقة"
    elif sec < 86400:
        h = sec // 3600
        m = (sec % 3600) // 60
        ago = f"منذ {h}س {m}د" if m else f"منذ {h} ساعة"
    else:
        ago = f"منذ {sec // 86400} يوم"
    clock = local.strftime("%H:%M")
    return {
        "appeared_ts": int(ts),
        "appeared_at": dt.isoformat(),
        "appeared_local": local.strftime("%Y-%m-%d %H:%M %Z"),
        "appeared_ny": ny.strftime("%H:%M %Z"),
        "appeared_clock_ar": clock,
        "appeared_ago_ar": ago,
        "appeared_ar": f"ظهر {ago} · {clock}",
    }


def track_sniper_appearances(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Persist first-seen time per symbol and attach appeared_* fields."""
    seen = _load_seen()
    now = datetime.now(timezone.utc).timestamp()
    dirty = False
    out: list[dict[str, Any]] = []
    for row in rows:
        sym = str(row.get("symbol") or "").upper()
        if not sym:
            continue
        if sym not in seen:
            seen[sym] = now
            dirty = True
        stamped = dict(row)
        stamped.update(_stamp_appeared(float(seen[sym])))
        out.append(stamped)
    if dirty or rows:
        _save_seen(seen)
    return out


def _sniper_live(symbol: str) -> dict[str, Any] | None:
    """Live last + full-day % vs previous close (not wiped in post by RTH ref)."""
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "1d", "interval": "1m", "includePrePost": "true"},
            headers=UA,
            timeout=15,
        )
        r.raise_for_status()
        res = ((r.json().get("chart") or {}).get("result") or [None])[0]
        if not res:
            return None
        meta = res.get("meta") or {}
        closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
        live = next((float(c) for c in reversed(closes) if c is not None), None)
        rth = float(meta.get("regularMarketPrice") or 0)
        prev_close = float(meta.get("chartPreviousClose") or meta.get("previousClose") or 0)
        if live is None or live <= 0:
            live = rth if rth > 0 else None
        if live is None or prev_close <= 0:
            return None
        # Sniper ranks on the full day move (pre→RTH→post), not AH drift alone
        chg = (live - prev_close) / prev_close * 100
        # Prefer Yahoo's regular % when AH last ≈ RTH (avoids tiny noise)
        reg_pct = meta.get("regularMarketChangePercent")
        if reg_pct is not None and abs(live - rth) / max(rth, 1e-9) < 0.01:
            chg = float(reg_pct)
        return {
            "symbol": symbol.upper(),
            "last": round(live, 4 if live < 1 else 2),
            "ref": round(prev_close, 4 if prev_close < 1 else 2),
            "change_pct": round(chg, 2),
            "rth_close": round(rth, 4 if rth and rth < 1 else 2) if rth else None,
            "phase": session_phase(),
            "name": (meta.get("shortName") or meta.get("longName") or "")[:40],
            "volume": float(meta.get("regularMarketVolume") or 0),
        }
    except Exception:
        return None


def sniper_passes(row: dict[str, Any], *, phase: str | None = None, has_news: bool = False) -> bool:
    last = float(row.get("last") or 0)
    chg = float(row.get("change_pct") or 0)
    dollar = float(row.get("dollar_volume") or 0)
    phase = phase or session_phase()
    if last < SNIPER_MIN_PRICE or last >= SNIPER_MAX_PRICE:
        return False
    if chg <= 0:
        return False
    floor = SNIPER_MIN_CHG_PRE if phase in ("pre", "post") else SNIPER_MIN_CHG
    if has_news:
        floor = min(floor, SNIPER_MIN_CHG_WITH_NEWS)
    if chg < floor:
        return False
    min_dol = SNIPER_MIN_DOLLAR_HOT if chg >= 15 else SNIPER_MIN_DOLLAR
    if dollar > 0 and dollar < min_dol:
        return False
    return True


def fetch_cheap_runners(*, limit: int = 25) -> list[dict[str, Any]]:
    """Yahoo day-gainers/actives re-ranked live, kept in cheap price band."""

    def _build() -> list[dict]:
        phase = session_phase()
        cand: list[str] = []
        # Prefer small-cap / actives first — day_gainers skews mid/large after the open
        for scr in ("small_cap_gainers", "most_actives", "day_gainers"):
            for s in _screener_symbols(scr, 40):
                if s and s not in cand:
                    cand.append(s)
        cand = cand[:70]
        lives: list[dict] = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {pool.submit(_sniper_live, s): s for s in cand}
            for fut in as_completed(futs):
                row = fut.result()
                if not row:
                    continue
                last = float(row["last"])
                if last < SNIPER_MIN_PRICE or last >= SNIPER_MAX_PRICE:
                    continue
                if float(row["change_pct"]) <= 0:
                    continue
                dollar = last * float(row.get("volume") or 0)
                lives.append(
                    {
                        "symbol": row["symbol"],
                        "name": row.get("name") or "",
                        "last": last,
                        "change_pct": float(row["change_pct"]),
                        "ref": row.get("ref"),
                        "volume": row.get("volume") or 0,
                        "dollar_volume": round(dollar, 2),
                        "dollar_volume_label": _money(dollar),
                        "phase": row.get("phase") or phase,
                        "session_ar": session_label_ar(row.get("phase") or phase),
                        "tv_url": f"https://www.tradingview.com/chart/?symbol={row['symbol']}",
                    }
                )
        # soft filter without main-board $15M floor
        lives = [x for x in lives if sniper_passes(x, phase=phase, has_news=False)]
        lives.sort(key=lambda x: x["change_pct"], reverse=True)
        return lives[:limit]

    cache_key = f"sniper_runners:{session_phase()}:{limit}"
    try:
        hit = cached_call(cache_key, _build, ttl=60)
        # Don't keep an empty board stuck for a full TTL minute
        if hit:
            return list(hit)
        return _build()
    except Exception:
        try:
            return _build()
        except Exception:
            return []


def build_sniper_scanner(
    *,
    runners: list[dict[str, Any]] | None = None,
    news: list[dict[str, Any]] | None = None,
    phase: str | None = None,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """
    Rank cheap runners; boost names with catalyst/news.
    Prefer news-backed spikes; still allow pure tape rockets if % is extreme.
    """
    phase = phase or session_phase()
    runners = list(runners or [])
    by_news: dict[str, list[dict[str, Any]]] = {}
    for n in news or []:
        sym = str(n.get("symbol") or "").upper()
        if not sym or sym == "MARKET":
            continue
        # skip hard-negative sentiment for long-only sniper
        if str(n.get("sentiment") or "") == "neg":
            continue
        by_news.setdefault(sym, []).append(n)
    for sym, rows in by_news.items():
        rows.sort(
            key=lambda x: (int(x.get("impact") or 0), float(x.get("published_ts") or 0)),
            reverse=True,
        )

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in runners:
        sym = str(r.get("symbol") or "").upper()
        if not sym or sym in seen:
            continue
        related = by_news.get(sym) or []
        has_news = bool(related)
        if not sniper_passes(r, phase=phase, has_news=has_news):
            continue
        top = related[0] if related else None
        chg = float(r.get("change_pct") or 0)
        if top and top.get("impact") is None:
            top = enrich_news_item(top, change_pct=chg)
        if top is None and chg >= 10:
            # tape rocket without headline yet
            impact = 4 if chg >= 20 else 3
            reason = "زخم سعري قوي بدون خبر رسمي مرفق بعد"
            cats: list[str] = []
            cat_keys: list[str] = []
            title = ""
            url = r.get("tv_url") or ""
        elif top is None:
            # require news for milder moves
            continue
        else:
            impact = int(top.get("impact") or 3)
            reason = str(top.get("impact_reason_ar") or top.get("title_ar") or top.get("title") or "")
            cats = list(top.get("catalyst_ar") or [])
            cat_keys = list(top.get("catalyst_keys") or [])
            title = str(top.get("title_ar") or top.get("title") or "")
            url = str(top.get("url") or r.get("tv_url") or "")

        # score: move × impact × cheapness boost × news boost
        last = float(r.get("last") or 0)
        cheap_boost = 1.35 if last < 1.0 else (1.15 if last < 3.0 else 1.0)
        news_boost = 1.4 if has_news else 1.0
        dollar = float(r.get("dollar_volume") or 0)
        dol_boost = 1.0 + min(dollar / 5_000_000.0, 2.0) * 0.15
        score = abs(chg) * max(impact, 1) * cheap_boost * news_boost * dol_boost

        alert = "قنص — زخم قوي"
        if has_news and impact >= 4:
            alert = "قنص + محفز قوي"
        elif has_news:
            alert = "قنص + خبر"
        elif chg >= 20:
            alert = "صاروخ سعري — راقب الخبر"

        out.append(
            {
                "symbol": sym,
                "name": r.get("name") or "",
                "last": round(last, 4 if last < 1 else 2),
                "change_pct": round(chg, 2),
                "dollar_volume": dollar,
                "dollar_volume_label": r.get("dollar_volume_label") or _money(dollar),
                "has_news": has_news,
                "impact": impact,
                "impact_ar": f"تأثير {impact}/5",
                "catalyst_ar": cats,
                "catalyst_keys": cat_keys,
                "news_title_ar": title,
                "news_url": url,
                "alert_ar": alert,
                "reason_ar": reason,
                "score": round(score, 2),
                "session_ar": r.get("session_ar") or session_label_ar(phase),
                "tv_url": r.get("tv_url") or f"https://www.tradingview.com/chart/?symbol={sym}",
                "tier": "sniper",
                "price_band_ar": f"${SNIPER_MIN_PRICE:.2f}–${SNIPER_MAX_PRICE:.0f}",
            }
        )
        seen.add(sym)

    out.sort(key=lambda x: (1 if x.get("has_news") else 0, float(x.get("score") or 0), float(x.get("change_pct") or 0)), reverse=True)
    return track_sniper_appearances(out[:limit])

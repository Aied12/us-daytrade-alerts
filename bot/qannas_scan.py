"""ماسح القناص — Gainers + Volume + Catalyst + Small/Mid Cap (+ Low Float).

شروط الصورة التعليمية:
- Gainers ≥ +20%
- Volume ≥ 2–3× المتوسط
- Small / Mid Cap
- Fresh Catalyst (خبر حقيقي وجديد)
- Low Float إن توفر (<20M مفضّل، <10M أقوى)

ترتيب الأولوية: خبر → سيولة → حجم الشركة/فلوت → استمرارية الارتفاع
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from bot.cacheutil import cached_call
from bot.catalyst_scan import enrich_news_item, impact_score_1_5
from bot.gainers import _money, session_label_ar, session_phase
from bot.market_data import QuoteSnapshot

UA = {"User-Agent": "Mozilla/5.0 us-daytrade-alerts/qannas"}
ROOT = Path(__file__).resolve().parent.parent
BOARD_PATH = ROOT / "data" / "qannas_board.json"
SEEN_PATH = ROOT / "data" / "qannas_seen.json"
RIYADH = ZoneInfo("Asia/Riyadh")
NY = ZoneInfo("America/New_York")

# —— شروط الماسح (من الصورة) ——
MIN_CHG_PCT = 20.0              # Gainers ≥ +20%
MIN_RVOL = 2.0                  # Volume ≥ 2× Avg (الحد الأدنى)
STRONG_RVOL = 3.0               # الأفضل ≥ 3×
MAX_MCAP = 10_000_000_000       # Mid-cap سقف (~$10B)
SMALL_MCAP = 2_000_000_000      # Small-cap تفضيل
FLOAT_GOOD = 20_000_000         # Low float إن توفر
FLOAT_GREAT = 10_000_000
MIN_DOLLAR = 2_000_000          # سيولة دولار دنيا (حماية)
MIN_PRICE = 0.25
MAX_PRICE = 150.0               # أبعد عن العمالقة
NEWS_MAX_AGE_H = 36.0           # خبر «جديد» تقريباً يوم ونصف
BOARD_MAX = 16
STICKY_HOLD_SEC = 25 * 60
SEEN_TTL_SEC = 20 * 3600

# خطة يومية بسيطة للمتابعة الورقية
PLAN_STOP_PCT = 0.07
PLAN_TP1_PCT = 0.06
PLAN_TP2_PCT = 0.14


def _px(n: float) -> float:
    n = float(n or 0)
    if n <= 0:
        return 0.0
    return round(n, 4 if n < 1 else 2)


def _fmt_cap(n: float | None) -> str:
    if not n or n <= 0:
        return "—"
    if n >= 1e9:
        return f"${n / 1e9:.2f}B"
    if n >= 1e6:
        return f"${n / 1e6:.0f}M"
    return f"${n:,.0f}"


def _fmt_float(n: float | None) -> str:
    if not n or n <= 0:
        return "—"
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        return f"{n / 1e3:.0f}K"
    return f"{n:.0f}"


def _load_json(path: Path, default: Any) -> Any:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data
    except Exception:
        return default


def _save_json(path: Path, data: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _screener_quotes(scr_id: str, count: int = 50) -> list[dict[str, Any]]:
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved",
            params={"scrIds": scr_id, "count": count, "formatted": "false"},
            headers=UA,
            timeout=20,
        )
        r.raise_for_status()
        return list(((r.json().get("finance") or {}).get("result") or [{}])[0].get("quotes") or [])
    except Exception:
        return []


def _live_row(symbol: str) -> dict[str, Any] | None:
    """Live % + volume + day range from Yahoo chart."""
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "5d", "interval": "1d", "includePrePost": "true"},
            headers=UA,
            timeout=15,
        )
        r.raise_for_status()
        res = ((r.json().get("chart") or {}).get("result") or [None])[0]
        if not res:
            return None
        meta = res.get("meta") or {}
        q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
        closes = q.get("close") or []
        vols = q.get("volume") or []
        highs = q.get("high") or []
        lows = q.get("low") or []
        opens = q.get("open") or []

        # average volume excluding today if possible
        hist_vols = [float(v) for v in vols[:-1] if v]
        avg_vol = (sum(hist_vols) / len(hist_vols)) if hist_vols else 0.0
        today_vol = float(vols[-1] or meta.get("regularMarketVolume") or 0)

        live = None
        # prefer intraday for live %
        try:
            r2 = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                params={"range": "1d", "interval": "5m", "includePrePost": "true"},
                headers=UA,
                timeout=12,
            )
            r2.raise_for_status()
            res2 = ((r2.json().get("chart") or {}).get("result") or [None])[0]
            if res2:
                c2 = ((res2.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
                live = next((float(c) for c in reversed(c2) if c is not None), None)
                meta2 = res2.get("meta") or {}
                if not today_vol:
                    today_vol = float(meta2.get("regularMarketVolume") or 0)
                meta = {**meta, **{k: v for k, v in meta2.items() if v}}
        except Exception:
            pass

        rth = float(meta.get("regularMarketPrice") or 0)
        prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or 0)
        if live is None or live <= 0:
            live = float(closes[-1]) if closes and closes[-1] is not None else rth
        phase = session_phase()
        if phase in ("pre", "post") and rth > 0:
            ref = rth
        else:
            ref = prev if prev > 0 else rth
        if not live or ref <= 0:
            return None
        chg = (live - ref) / ref * 100.0
        day_high = float(highs[-1]) if highs and highs[-1] is not None else float(meta.get("regularMarketDayHigh") or live)
        day_low = float(lows[-1]) if lows and lows[-1] is not None else float(meta.get("regularMarketDayLow") or live)
        day_open = float(opens[-1]) if opens and opens[-1] is not None else live
        rvol = (today_vol / avg_vol) if avg_vol > 0 else 0.0
        return {
            "symbol": symbol.upper(),
            "name": (meta.get("shortName") or meta.get("longName") or "")[:48],
            "last": _px(live),
            "ref": _px(ref),
            "change_pct": round(chg, 2),
            "volume": today_vol,
            "avg_volume": avg_vol,
            "rvol": round(rvol, 2),
            "day_high": _px(day_high),
            "day_low": _px(day_low),
            "day_open": _px(day_open),
            "phase": phase,
        }
    except Exception:
        return None


def _plan(last: float, day_low: float | None = None) -> dict[str, float]:
    entry = _px(last)
    stop = _px(entry * (1 - PLAN_STOP_PCT))
    if day_low and day_low > 0:
        stop = min(stop, _px(float(day_low) * 0.995))
    if stop >= entry:
        stop = _px(entry * 0.97)
    tp1 = _px(entry * (1 + PLAN_TP1_PCT))
    tp2 = _px(entry * (1 + PLAN_TP2_PCT))
    return {"entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2}


def _news_by_symbol(news: list[dict[str, Any]] | None) -> dict[str, list[dict[str, Any]]]:
    now = time.time()
    out: dict[str, list[dict[str, Any]]] = {}
    for n in news or []:
        sym = str(n.get("symbol") or "").upper()
        if not sym or sym == "MARKET":
            continue
        if str(n.get("sentiment") or "") == "neg":
            continue
        # freshness
        ts = float(n.get("published_ts") or n.get("ts") or 0)
        if ts and (now - ts) > NEWS_MAX_AGE_H * 3600:
            continue
        # need catalyst tags or impact
        tags = n.get("catalysts") or n.get("catalyst_tags") or []
        impact = int(n.get("impact") or 0)
        title = str(n.get("title") or n.get("headline") or "")
        if not tags and impact < 3 and title:
            # try detect
            _, tags2, _ = impact_score_1_5(title=title, summary=str(n.get("summary") or ""))
            tags = tags2
            if not impact:
                impact = max((t.get("base_impact") or 0 for t in tags2), default=0)
        if not tags and impact < 3:
            continue
        row = dict(n)
        row["impact"] = impact
        row["catalysts"] = tags
        out.setdefault(sym, []).append(row)
    for sym, rows in out.items():
        rows.sort(
            key=lambda x: (int(x.get("impact") or 0), float(x.get("published_ts") or 0)),
            reverse=True,
        )
    return out


def _checklist(
    *,
    has_news: bool,
    rvol: float,
    mcap: float | None,
    float_shares: float | None,
    change_pct: float,
    day_open: float,
    last: float,
) -> list[dict[str, Any]]:
    """ترتيب الأولوية من الصورة."""
    above_open = bool(day_open and last and last >= day_open * 0.995)
    smallish = bool(mcap and mcap <= SMALL_MCAP) or bool(float_shares and float_shares <= FLOAT_GOOD)
    return [
        {"key": "news", "ar": "خبر حقيقي وجديد", "ok": has_news},
        {"key": "liq", "ar": "سيولة قوية (حجم فوق المتوسط)", "ok": rvol >= MIN_RVOL},
        {"key": "size", "ar": "شركة صغيرة / فلوت ضيق", "ok": smallish},
        {
            "key": "trend",
            "ar": "الارتفاع مستمر (فوق الافتتاح)",
            "ok": above_open and change_pct >= MIN_CHG_PCT,
        },
    ]


def _score_row(row: dict[str, Any]) -> float:
    chg = float(row.get("change_pct") or 0)
    rvol = float(row.get("rvol") or 0)
    mcap = float(row.get("market_cap") or 0)
    flt = float(row.get("float_shares") or 0)
    impact = int(row.get("news_impact") or 0)
    has_news = bool(row.get("has_news"))
    score = 0.0
    score += min(chg, 80) * 1.2
    score += min(rvol, 10) * 8
    if has_news:
        score += 25 + impact * 4
    if mcap and mcap <= SMALL_MCAP:
        score += 12
    elif mcap and mcap <= MAX_MCAP:
        score += 6
    if flt and flt <= FLOAT_GREAT:
        score += 14
    elif flt and flt <= FLOAT_GOOD:
        score += 8
    # checklist completeness
    checks = row.get("checklist") or []
    score += sum(8 for c in checks if c.get("ok"))
    return round(score, 2)


def fetch_qannas_universe(*, limit: int = 40) -> list[dict[str, Any]]:
    """Top gainers / small-cap gainers / actives → live filter."""

    def _build() -> list[dict[str, Any]]:
        by: dict[str, dict[str, Any]] = {}
        for scr in ("day_gainers", "small_cap_gainers", "most_actives", "aggressive_small_caps"):
            for q in _screener_quotes(scr, 50):
                sym = str(q.get("symbol") or "").upper()
                if not sym or not sym.isalpha() or len(sym) > 5:
                    continue
                prev = by.get(sym) or {}
                by[sym] = {
                    **prev,
                    "symbol": sym,
                    "market_cap": float(q.get("marketCap") or prev.get("market_cap") or 0),
                    "screener_chg": float(q.get("regularMarketChangePercent") or prev.get("screener_chg") or 0),
                    "screener_price": float(q.get("regularMarketPrice") or prev.get("screener_price") or 0),
                    "float_shares": float(
                        q.get("floatShares")
                        or q.get("sharesOutstanding")
                        or prev.get("float_shares")
                        or 0
                    ),
                    "name": q.get("shortName") or q.get("longName") or prev.get("name") or "",
                }

        # prefilter soft by screener % / mcap
        cands = []
        for sym, q in by.items():
            mcap = float(q.get("market_cap") or 0)
            if mcap and mcap > MAX_MCAP * 1.25:
                continue
            px = float(q.get("screener_price") or 0)
            if px and (px < MIN_PRICE or px > MAX_PRICE):
                continue
            chg = float(q.get("screener_chg") or 0)
            if chg < 8:  # soft — live may be higher
                continue
            cands.append(sym)
        cands = cands[:90]

        lives: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=10) as pool:
            futs = {pool.submit(_live_row, s): s for s in cands}
            for fut in as_completed(futs):
                sym = futs[fut]
                live = fut.result()
                if not live:
                    continue
                base = by.get(sym) or {}
                last = float(live["last"])
                chg = float(live["change_pct"])
                rvol = float(live.get("rvol") or 0)
                vol = float(live.get("volume") or 0)
                dollar = last * vol
                mcap = float(base.get("market_cap") or 0)
                if chg < MIN_CHG_PCT:
                    continue
                if last < MIN_PRICE or last > MAX_PRICE:
                    continue
                if mcap and mcap > MAX_MCAP:
                    continue
                if rvol < MIN_RVOL and dollar < MIN_DOLLAR * 3:
                    continue
                if dollar < MIN_DOLLAR:
                    continue
                lives.append(
                    {
                        **live,
                        "name": live.get("name") or base.get("name") or "",
                        "market_cap": mcap or None,
                        "market_cap_label": _fmt_cap(mcap) if mcap else "—",
                        "float_shares": float(base.get("float_shares") or 0) or None,
                        "float_label": _fmt_float(float(base.get("float_shares") or 0) or None),
                        "dollar_volume": round(dollar, 2),
                        "dollar_volume_label": _money(dollar),
                        "session_ar": session_label_ar(live.get("phase")),
                        "tv_url": f"https://www.tradingview.com/chart/?symbol={sym}",
                        "cap_bucket": (
                            "small" if mcap and mcap <= SMALL_MCAP else ("mid" if mcap else "unknown")
                        ),
                    }
                )
        lives.sort(key=lambda x: -float(x.get("change_pct") or 0))
        return lives[:limit]

    key = f"qannas_universe:{session_phase()}:{limit}"
    try:
        hit = cached_call(key, _build, ttl=50)
        return list(hit) if hit else _build()
    except Exception:
        try:
            return _build()
        except Exception:
            return []


def build_qannas_scanner(
    *,
    runners: list[dict[str, Any]] | None = None,
    news: list[dict[str, Any]] | None = None,
    snaps: list[QuoteSnapshot] | None = None,
    phase: str | None = None,
    limit: int = 12,
    require_news: bool = True,
) -> list[dict[str, Any]]:
    """رتّب بطاقات القناص وفق شروط الصورة + قائمة أولوية."""
    phase = phase or session_phase()
    runners = list(runners if runners is not None else fetch_qannas_universe(limit=40))
    by_news = _news_by_symbol(news)

    # enrich rvol from watchlist snaps when available
    snap_by = {s.symbol.upper(): s for s in (snaps or []) if getattr(s, "symbol", None)}

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    now = time.time()

    for r in runners:
        sym = str(r.get("symbol") or "").upper()
        if not sym or sym in seen:
            continue
        snap = snap_by.get(sym)
        rvol = float(r.get("rvol") or 0)
        if snap and snap.avg_volume_20 > 0 and snap.volume > 0:
            rvol = max(rvol, snap.volume / snap.avg_volume_20)
        if rvol < MIN_RVOL:
            continue

        related = by_news.get(sym) or []
        has_news = bool(related)
        if require_news and not has_news:
            continue

        mcap = float(r.get("market_cap") or 0) or None
        flt = float(r.get("float_shares") or 0) or None
        chg = float(r.get("change_pct") or 0)
        last = float(r.get("last") or 0)
        day_open = float(r.get("day_open") or 0)
        day_low = float(r.get("day_low") or 0) or None

        top_news = related[0] if related else None
        news_impact = int((top_news or {}).get("impact") or 0)
        news_title = str((top_news or {}).get("title") or (top_news or {}).get("headline") or "")
        catalysts = (top_news or {}).get("catalysts") or []

        checklist = _checklist(
            has_news=has_news,
            rvol=rvol,
            mcap=mcap,
            float_shares=flt,
            change_pct=chg,
            day_open=day_open,
            last=last,
        )
        checks_ok = sum(1 for c in checklist if c.get("ok"))
        plan = _plan(last, day_low)

        low_float = bool(flt and flt <= FLOAT_GOOD)
        row = {
            "symbol": sym,
            "name": r.get("name") or "",
            "last": _px(last),
            "change_pct": round(chg, 2),
            "rvol": round(rvol, 2),
            "volume": float(r.get("volume") or 0),
            "avg_volume": float(r.get("avg_volume") or 0),
            "dollar_volume": float(r.get("dollar_volume") or 0),
            "dollar_volume_label": r.get("dollar_volume_label") or _money(float(r.get("dollar_volume") or 0)),
            "market_cap": mcap,
            "market_cap_label": r.get("market_cap_label") or _fmt_cap(mcap),
            "cap_bucket": r.get("cap_bucket") or "unknown",
            "float_shares": flt,
            "float_label": r.get("float_label") or _fmt_float(flt),
            "low_float": low_float,
            "has_news": has_news,
            "news_title": news_title[:140],
            "news_impact": news_impact,
            "catalysts": catalysts[:4],
            "catalyst_ar": " · ".join(
                str(t.get("ar") or t.get("key") or "") for t in catalysts[:3] if t
            ),
            "checklist": checklist,
            "checks_ok": checks_ok,
            "checks_total": 4,
            "complete": checks_ok >= 3 and has_news and rvol >= MIN_RVOL and chg >= MIN_CHG_PCT,
            "entry": plan["entry"],
            "stop": plan["stop"],
            "tp1": plan["tp1"],
            "tp2": plan["tp2"],
            "side": "long",
            "strategies": ["القناص", "محفز + زخم"],
            "tag_ar": "🎯 القناص",
            "session_ar": r.get("session_ar") or session_label_ar(phase),
            "tv_url": r.get("tv_url") or f"https://www.tradingview.com/chart/?symbol={sym}",
            "phase": phase,
            "updated_ts": int(now),
        }
        row["score"] = _score_row(row)
        out.append(row)
        seen.add(sym)

    out.sort(
        key=lambda x: (
            0 if x.get("complete") else 1,
            -int(x.get("checks_ok") or 0),
            -float(x.get("score") or 0),
            -float(x.get("change_pct") or 0),
        )
    )
    board = _sticky_merge(out[:limit])
    _save_json(BOARD_PATH, {"updated_ts": int(now), "items": board})
    return board


def _sticky_merge(fresh: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep cards briefly so the board does not flicker on every tick."""
    now = time.time()
    prev = _load_json(BOARD_PATH, {})
    old_items = list((prev or {}).get("items") or [])
    by_old = {str(x.get("symbol") or "").upper(): x for x in old_items}
    seen = _load_json(SEEN_PATH, {})
    if not isinstance(seen, dict):
        seen = {}

    out: list[dict[str, Any]] = []
    have: set[str] = set()
    for row in fresh:
        sym = str(row.get("symbol") or "").upper()
        if not sym:
            continue
        first = float(seen.get(sym) or by_old.get(sym, {}).get("first_ts") or now)
        seen[sym] = first
        row = dict(row)
        row["first_ts"] = int(first)
        out.append(row)
        have.add(sym)

    # sticky keep
    for sym, old in by_old.items():
        if sym in have:
            continue
        last_ok = float(old.get("updated_ts") or old.get("first_ts") or 0)
        if now - last_ok > STICKY_HOLD_SEC:
            continue
        # still roughly elevated
        if float(old.get("change_pct") or 0) < MIN_CHG_PCT * 0.55:
            continue
        sticky = dict(old)
        sticky["sticky"] = True
        out.append(sticky)
        have.add(sym)

    # prune seen
    seen = {k: float(v) for k, v in seen.items() if now - float(v) < SEEN_TTL_SEC}
    _save_json(SEEN_PATH, seen)

    out.sort(
        key=lambda x: (
            0 if x.get("complete") else 1,
            -int(x.get("checks_ok") or 0),
            -float(x.get("score") or 0),
        )
    )
    return out[:BOARD_MAX]


NOTE_AR = (
    "القناص: Gainers ≥+20% · Volume ≥2–3× · Small/Mid Cap · خبر محفز جديد"
    " · Low Float إن توفر — ترتيب: خبر → سيولة → حجم الشركة → استمرارية"
)

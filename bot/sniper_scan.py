"""Hessa-style sniper: automatic «ملكة السنتات» board (no manual picks).

Fully automated on every always-on tick. Independent of the dashboard
price-tier buttons (≥$51 and $5–$51) — sniper only covers under $5:

- Primary: stocks under $1 (سنتات)
- Secondary: $1 – under $5
- Strong day momentum + preferred news/catalyst
- Auto trade card: دخول / وقف / جني / دعم / مقاومة
- Long-only day-trade; separate from the conservative liquid board
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
BOARD_PATH = ROOT / "data" / "sniper_board.json"
RIYADH = ZoneInfo("Asia/Riyadh")
NY = ZoneInfo("America/New_York")

# —— Sniper universe: تحت $5 فقط (خارج شرائح ≥$51 و $5–$51) ——
SNIPER_MIN_PRICE = 0.10
SNIPER_CENTS_MAX = 1.00          # سنتات
SNIPER_MAX_PRICE = 4.999         # أقل من $5 — لا يدخل شريحة $5–$51
SNIPER_PRIME_MAX = SNIPER_MAX_PRICE  # $1 – <$5

# Momentum floors (day % vs prior close)
SNIPER_MIN_CHG = 8.0             # بدون خبر — حركة واضحة
SNIPER_MIN_CHG_WITH_NEWS = 4.0   # مع محفز يُقبل أبكر
SNIPER_MIN_CHG_PRE = 5.0         # pre/post
SNIPER_MIN_CHG_ROCKET = 15.0
# خروج أنعم من الدخول — يمنع اختفاء/رجوع السهم حول العتبة
SNIPER_KEEP_CHG = 3.0

# Participation — cents often thin; still require real prints
SNIPER_MIN_DOLLAR = 250_000
SNIPER_MIN_DOLLAR_HOT = 120_000  # if move is huge

# Auto plan (long day-trade) — نسب قنص السنتات
PLAN_STOP_PCT = 0.07             # وقف ≈ 7% تحت الدخول
PLAN_TP1_PCT = 0.06              # جني1 ≈ +6% (قريب وقابل للتحقق)
PLAN_TP2_PCT = 0.14              # جني2 ≈ +14% للامتداد / الصاروخ

SEEN_TTL_SEC = 20 * 3600
# بعد الظهور: ابقِ السهم على اللوحة حتى لو ضعف الزخم مؤقتاً
STICKY_HOLD_SEC = 30 * 60
BOARD_MAX = 20


def _px(n: float) -> float:
    n = float(n)
    if n <= 0:
        return 0.0
    return round(n, 4 if n < 1 else 2)


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


def _load_board() -> dict[str, Any]:
    try:
        data = json.loads(BOARD_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_board(board: dict[str, Any]) -> None:
    try:
        BOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
        # trim oldest by last_ok_ts
        items = sorted(
            ((k, v) for k, v in board.items() if isinstance(v, dict)),
            key=lambda kv: float(kv[1].get("last_ok_ts") or 0),
            reverse=True,
        )[:BOARD_MAX]
        BOARD_PATH.write_text(
            json.dumps(dict(items), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def sniper_keep_alive(row: dict[str, Any], *, phase: str | None = None) -> bool:
    """Softer exit rule so borderline % (e.g. 15.5→14.9) لا يمسح البطاقة."""
    last = float(row.get("last") or 0)
    chg = float(row.get("change_pct") or 0)
    if sniper_tier(last) is None:
        return False
    if chg <= 0:
        return False
    phase = phase or session_phase()
    floor = SNIPER_KEEP_CHG
    if phase in ("pre", "post"):
        floor = min(floor, 2.0)
    return chg >= floor


def apply_sniper_sticky(
    fresh: list[dict[str, Any]],
    *,
    runners_by_sym: dict[str, dict[str, Any]] | None = None,
    phase: str | None = None,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """
    Merge new hits with previously shown names.
    Keeps a symbol on the board for STICKY_HOLD_SEC while still in-band & green.
    """
    phase = phase or session_phase()
    now = datetime.now(timezone.utc).timestamp()
    board = _load_board()
    runners_by_sym = runners_by_sym or {}

    fresh_by: dict[str, dict[str, Any]] = {}
    for row in fresh:
        sym = str(row.get("symbol") or "").upper()
        if sym:
            fresh_by[sym] = row
            board[sym] = {
                "last_ok_ts": now,
                "first_ts": float((board.get(sym) or {}).get("first_ts") or now),
                "row": row,
            }

    # refresh sticky names that didn't re-qualify this tick
    for sym, meta in list(board.items()):
        if not isinstance(meta, dict):
            board.pop(sym, None)
            continue
        if sym in fresh_by:
            continue
        last_ok = float(meta.get("last_ok_ts") or 0)
        if now - last_ok > STICKY_HOLD_SEC:
            board.pop(sym, None)
            continue
        live = runners_by_sym.get(sym) or (meta.get("row") if isinstance(meta.get("row"), dict) else None)
        if not live or not sniper_keep_alive(live, phase=phase):
            board.pop(sym, None)
            continue
        # keep previous card, refresh price/% if we have live runner
        prev = dict(meta.get("row") or {})
        if live:
            prev["last"] = _px(float(live.get("last") or prev.get("last") or 0))
            prev["change_pct"] = round(float(live.get("change_pct") or prev.get("change_pct") or 0), 2)
            prev["dollar_volume"] = float(live.get("dollar_volume") or prev.get("dollar_volume") or 0)
            prev["dollar_volume_label"] = live.get("dollar_volume_label") or prev.get("dollar_volume_label")
            prev["session_ar"] = live.get("session_ar") or prev.get("session_ar")
            plan = build_hessa_plan(
                last=float(prev["last"] or 0),
                day_low=live.get("day_low"),
                day_high=live.get("day_high"),
            )
            prev.update(plan)
            prev["sticky"] = True
            prev["alert_ar"] = prev.get("alert_ar") or "قنص — مثبت مؤقتاً"
        board[sym] = {
            "last_ok_ts": last_ok,  # لا تمدد بدون تأهيل جديد
            "first_ts": float(meta.get("first_ts") or now),
            "row": prev,
        }
        fresh_by[sym] = prev

    _save_board(board)

    merged = list(fresh_by.values())
    merged.sort(
        key=lambda x: (
            0 if x.get("tier_key") == "cents" else 1,
            0 if x.get("has_news") else 1,
            -float(x.get("score") or 0),
            -float(x.get("change_pct") or 0),
        )
    )
    return merged[:limit]


def build_hessa_plan(
    *,
    last: float,
    day_low: float | None = None,
    day_high: float | None = None,
) -> dict[str, Any]:
    """
    Auto levels in the spirit of her share cards:
    دخول عند السعر الحي، وقف تحت الدعم، جني على مرحلتين، دعم/مقاومة من نطاق اليوم.
    """
    entry = _px(last)
    if entry <= 0:
        return {}
    stop_raw = entry * (1.0 - PLAN_STOP_PCT)
    if day_low and day_low > 0:
        # لا نضع الوقف فوق قاع اليوم إن كان أضيق من 7%
        stop_raw = min(stop_raw, float(day_low) * 0.995)
        # ولا نجعله أعمق من ~12% في السنتات الضيقة
        stop_raw = max(stop_raw, entry * 0.88)
    tp1 = entry * (1.0 + PLAN_TP1_PCT)
    tp2 = entry * (1.0 + PLAN_TP2_PCT)
    support = _px(day_low) if day_low and day_low > 0 else _px(stop_raw)
    resistance = _px(day_high) if day_high and day_high > entry else _px(tp1)
    stop = _px(stop_raw)
    return {
        "entry": entry,
        "stop": stop,
        "tp1": _px(tp1),
        "tp2": _px(tp2),
        "support": support,
        "resistance": resistance,
        "plan_ar": (
            f"دخول ${_px(entry)} · وقف ${_px(stop)} · "
            f"جني1 ${_px(tp1)} · جني2 ${_px(tp2)}"
        ),
        "levels_ar": (
            f"دعم ${_px(support)} · مقاومة ${_px(resistance)}"
        ),
        "auto_plan": True,
        "style": "hessa_cents",
    }


def _sniper_live(symbol: str) -> dict[str, Any] | None:
    """Live last + full-day % vs previous close + day range for plan levels."""
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
        quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
        closes = quote.get("close") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        live = next((float(c) for c in reversed(closes) if c is not None), None)
        rth = float(meta.get("regularMarketPrice") or 0)
        prev_close = float(meta.get("chartPreviousClose") or meta.get("previousClose") or 0)
        day_high = float(meta.get("regularMarketDayHigh") or 0) or None
        day_low = float(meta.get("regularMarketDayLow") or 0) or None
        if not day_high:
            vals = [float(h) for h in highs if h is not None]
            day_high = max(vals) if vals else None
        if not day_low:
            vals = [float(lo) for lo in lows if lo is not None]
            day_low = min(vals) if vals else None
        if live is None or live <= 0:
            live = rth if rth > 0 else None
        if live is None or prev_close <= 0:
            return None
        chg = (live - prev_close) / prev_close * 100
        reg_pct = meta.get("regularMarketChangePercent")
        if reg_pct is not None and abs(live - rth) / max(rth, 1e-9) < 0.01:
            chg = float(reg_pct)
        return {
            "symbol": symbol.upper(),
            "last": _px(live),
            "ref": _px(prev_close),
            "change_pct": round(chg, 2),
            "rth_close": _px(rth) if rth else None,
            "day_high": _px(day_high) if day_high else None,
            "day_low": _px(day_low) if day_low else None,
            "phase": session_phase(),
            "name": (meta.get("shortName") or meta.get("longName") or "")[:40],
            "volume": float(meta.get("regularMarketVolume") or 0),
        }
    except Exception:
        return None


def sniper_tier(last: float) -> str | None:
    last = float(last or 0)
    if last < SNIPER_MIN_PRICE or last > SNIPER_MAX_PRICE:
        return None
    if last < SNIPER_CENTS_MAX:
        return "cents"       # سنتات
    return "prime"           # $1 – <$5


def sniper_passes(row: dict[str, Any], *, phase: str | None = None, has_news: bool = False) -> bool:
    last = float(row.get("last") or 0)
    chg = float(row.get("change_pct") or 0)
    dollar = float(row.get("dollar_volume") or 0)
    phase = phase or session_phase()
    if sniper_tier(last) is None:
        return False
    if chg <= 0:
        return False
    floor = SNIPER_MIN_CHG_PRE if phase in ("pre", "post") else SNIPER_MIN_CHG
    if has_news:
        floor = min(floor, SNIPER_MIN_CHG_WITH_NEWS)
    if chg < floor:
        return False
    min_dol = SNIPER_MIN_DOLLAR_HOT if chg >= SNIPER_MIN_CHG_ROCKET else SNIPER_MIN_DOLLAR
    if dollar > 0 and dollar < min_dol:
        return False
    return True


def fetch_cheap_runners(*, limit: int = 30) -> list[dict[str, Any]]:
    """Yahoo day-gainers/actives re-ranked live — Hessa universe ≤ $2."""

    def _build() -> list[dict]:
        phase = session_phase()
        cand: list[str] = []
        for scr in ("small_cap_gainers", "most_actives", "day_gainers"):
            try:
                for s in _screener_symbols(scr, 50):
                    if s and s not in cand:
                        cand.append(s)
            except Exception:
                continue
        cand = cand[:90]
        lives: list[dict] = []
        with ThreadPoolExecutor(max_workers=10) as pool:
            futs = {pool.submit(_sniper_live, s): s for s in cand}
            for fut in as_completed(futs):
                row = fut.result()
                if not row:
                    continue
                last = float(row["last"])
                if sniper_tier(last) is None:
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
                        "day_high": row.get("day_high"),
                        "day_low": row.get("day_low"),
                        "volume": row.get("volume") or 0,
                        "dollar_volume": round(dollar, 2),
                        "dollar_volume_label": _money(dollar),
                        "phase": row.get("phase") or phase,
                        "session_ar": session_label_ar(row.get("phase") or phase),
                        "tv_url": f"https://www.tradingview.com/chart/?symbol={row['symbol']}",
                        "tier_key": sniper_tier(last),
                    }
                )
        lives = [x for x in lives if sniper_passes(x, phase=phase, has_news=False)]
        # Prefer cents, then % move
        lives.sort(
            key=lambda x: (
                0 if x.get("tier_key") == "cents" else 1,
                -float(x["change_pct"]),
            )
        )
        return lives[:limit]

    cache_key = f"sniper_hessa_runners:{session_phase()}:{limit}"
    try:
        hit = cached_call(cache_key, _build, ttl=45)
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
    Auto-rank Hessa-style cents/prime runners.
    Prefer news-backed spikes; allow pure tape rockets if % is extreme.
    Attaches automatic entry/stop/targets — no manual intervention.
    """
    phase = phase or session_phase()
    runners = list(runners or [])
    by_news: dict[str, list[dict[str, Any]]] = {}
    for n in news or []:
        sym = str(n.get("symbol") or "").upper()
        if not sym or sym == "MARKET":
            continue
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
        if top is None and chg >= SNIPER_MIN_CHG_ROCKET:
            impact = 4 if chg >= 25 else 3
            reason = "زخم سنتات قوي — راقب الخبر/النشاط"
            cats: list[str] = []
            cat_keys: list[str] = []
            title = ""
            url = r.get("tv_url") or ""
        elif top is None:
            # كان الشرط صاروخ ≥15% فقط فيسبب اختفاء/رجوع حول العتبة (مثل 15.5↔14.9)
            impact = 3
            reason = "زخم سعري ضمن نطاق القنص — راقب الخبر/النشاط"
            cats = []
            cat_keys = []
            title = ""
            url = r.get("tv_url") or ""
        else:
            impact = int(top.get("impact") or 3)
            reason = str(top.get("impact_reason_ar") or top.get("title_ar") or top.get("title") or "")
            cats = list(top.get("catalyst_ar") or [])
            cat_keys = list(top.get("catalyst_keys") or [])
            title = str(top.get("title_ar") or top.get("title") or "")
            url = str(top.get("url") or r.get("tv_url") or "")

        last = float(r.get("last") or 0)
        tier = sniper_tier(last) or "prime"
        cheap_boost = 1.55 if tier == "cents" else 1.15
        news_boost = 1.45 if has_news else 1.0
        dollar = float(r.get("dollar_volume") or 0)
        dol_boost = 1.0 + min(dollar / 3_000_000.0, 2.0) * 0.15
        score = abs(chg) * max(impact, 1) * cheap_boost * news_boost * dol_boost

        if tier == "cents":
            alert = "قنص سنتات — طرح تلقائي"
        else:
            alert = "قنص تحت $5 — طرح تلقائي"
        if has_news and impact >= 4:
            alert = ("سنتات" if tier == "cents" else "تحت $5") + " + محفز قوي"
        elif has_news:
            alert = ("سنتات" if tier == "cents" else "تحت $5") + " + خبر/نشاط"
        elif chg >= SNIPER_MIN_CHG_ROCKET:
            alert = "صاروخ سنتات — راقب الخبر"

        plan = build_hessa_plan(
            last=last,
            day_low=r.get("day_low"),
            day_high=r.get("day_high"),
        )

        out.append(
            {
                "symbol": sym,
                "name": r.get("name") or "",
                "last": _px(last),
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
                "tier_key": tier,
                "tier_ar": "سنتات (<$1)" if tier == "cents" else "تحت $5 ($1–<$5)",
                "price_band_ar": f"${SNIPER_MIN_PRICE:.2f}–<$5",
                "auto": True,
                "source_style": "hessa_cents_auto",
                "sticky": False,
                **plan,
            }
        )
        seen.add(sym)

    runners_by_sym = {
        str(r.get("symbol") or "").upper(): r
        for r in runners
        if r.get("symbol")
    }
    out = apply_sniper_sticky(
        out,
        runners_by_sym=runners_by_sym,
        phase=phase,
        limit=limit,
    )
    return track_sniper_appearances(out)

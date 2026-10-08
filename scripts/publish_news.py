#!/usr/bin/env python3
"""High-frequency stock news: up to ~5 new Arabic headlines per minute."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.config import load_settings
from bot.news_ar import (
    _is_good_ar,
    ensure_news_arabic,
    fetch_stock_news_ar,
    load_seen_news,
    news_fingerprint,
    purge_bad_news_ar_cache,
    save_seen_news,
)

def run(cmd: list[str]) -> int:
    print("+", " ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT)


JUNK_FEED_SYMBOLS = frozenset(
    {
        "MARKET", "GLOBE", "EEST", "EST", "EDT", "UTC", "GMT", "CEO", "FDA", "SEC",
        "NYSE", "NASDAQ", "OTC", "PR", "NEWS", "HTTP", "HTTPS", "WWW", "COM",
    }
)


def _is_real_ticker(sym: str) -> bool:
    s = str(sym or "").upper().strip()
    if not s or s in JUNK_FEED_SYMBOLS:
        return False
    if not s.isalpha():
        return False
    return 1 <= len(s) <= 5


def _symbols(settings) -> list[str]:
    syms = list(settings.watchlist or [])
    try:
        status = json.loads((ROOT / "docs" / "status.json").read_text(encoding="utf-8"))
        for o in (status.get("opportunities") or [])[:12]:
            if o.get("symbol"):
                syms.append(str(o["symbol"]).upper())
        for g in (status.get("gainers") or [])[:12]:
            if g.get("symbol"):
                syms.append(str(g["symbol"]).upper())
        for q in (status.get("qannas_scanner") or [])[:16]:
            if q.get("symbol"):
                syms.append(str(q["symbol"]).upper())
    except Exception:
        pass
    # قناص board — أهم مصدر لربط الخبر بالأسهم المتحركة
    try:
        board = json.loads((ROOT / "data" / "qannas_board.json").read_text(encoding="utf-8"))
        for q in (board.get("items") or [])[:20]:
            if q.get("symbol"):
                syms.append(str(q["symbol"]).upper())
    except Exception:
        pass
    try:
        live = json.loads((ROOT / "docs" / "live.json").read_text(encoding="utf-8"))
        for q in (live.get("qannas_scanner") or [])[:16]:
            if q.get("symbol"):
                syms.append(str(q["symbol"]).upper())
        for g in (live.get("gainers") or [])[:15]:
            if g.get("symbol"):
                syms.append(str(g["symbol"]).upper())
    except Exception:
        pass
    # liquid movers often in the news
    for s in ("SPY", "QQQ", "NVDA", "TSLA", "AMD", "AAPL", "META", "AMZN", "MSFT", "PLTR", "COIN", "BA"):
        syms.append(s)
    return list(dict.fromkeys(s.upper() for s in syms if s and str(s).isalpha() and _is_real_ticker(s)))[:28]


def _write_news_live(items: list[dict], *, fresh_n: int) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_ts": int(time.time()),
        "fresh_this_minute": fresh_n,
        "count": len(items),
        "note_ar": "أخبار بأسهم حقيقية فقط · بدون MARKET · تأثير 1–5",
        "news": items,
    }
    raw = json.dumps(payload, ensure_ascii=False)
    for d in (ROOT / "docs", ROOT / "pages"):
        d.mkdir(parents=True, exist_ok=True)
        (d / "news-live.json").write_text(raw, encoding="utf-8")


def main() -> int:
    settings = load_settings()
    # Cleanup poisoned EN-as-AR cache at most once per day
    gate = ROOT / "data" / "news_ar_cache_purged.json"
    try:
        import datetime as _dt

        today = _dt.date.today().isoformat()
        prev_day = ""
        if gate.exists():
            prev_day = str(json.loads(gate.read_text(encoding="utf-8")).get("day") or "")
        if prev_day != today:
            purged = purge_bad_news_ar_cache()
            gate.parent.mkdir(parents=True, exist_ok=True)
            gate.write_text(json.dumps({"day": today, "purged": purged}, ensure_ascii=False), encoding="utf-8")
            if purged:
                print(f"[news] purged bad ar-cache entries={purged}", flush=True)
    except Exception as e:
        print(f"[news] cache purge skip: {e}", flush=True)

    syms = _symbols(settings)
    pack = fetch_stock_news_ar(
        syms,
        limit=50,
        per_symbol=3,
        drop_negative_symbols=False,  # feed volume: drop only neg headlines
        translate_summary=False,
        include_market=False,  # لا نملأ اللوحة بـ MARKET من أسلاك عامة
    )
    news = [n for n in (pack.get("news") or []) if n.get("sentiment") in ("pos", "neu")]
    # أسلاك StockTitan: أبقِ فقط ما له رمز حقيقي
    try:
        from bot.stocktitan_news import fetch_stocktitan_style_news

        for row in fetch_stocktitan_style_news(limit=40):
            if str(row.get("sentiment") or "neu") not in ("pos", "neu"):
                continue
            if not _is_real_ticker(str(row.get("symbol") or "")):
                continue
            news.append(row)
    except Exception:
        pass
    try:
        from bot.catalyst_scan import enrich_news_item, rank_catalyst_news

        news = rank_catalyst_news(
            [enrich_news_item(n) for n in news if _is_real_ticker(str(n.get("symbol") or ""))],
            limit=50,
        )
    except Exception:
        news = [n for n in news if _is_real_ticker(str(n.get("symbol") or ""))]

    seen = load_seen_news()
    now = time.time()
    fresh: list[dict] = []
    for n in news:
        fp = n.get("id") or news_fingerprint(n.get("title") or "", n.get("symbol") or "")
        n["id"] = fp
        if fp in seen:
            continue
        fresh.append(n)
        seen[fp] = now

    # Cap Telegram burst at 5/minute (old-bot cadence)
    burst = fresh[:5]
    # Site-only: do not push news to Telegram
    print(f"[news] site-only fresh={len(fresh)} shown_new={len(burst)}")

    # Rolling board: newest → oldest — keep any existing good Arabic titles
    prev: list[dict] = []
    try:
        prev = list(json.loads((ROOT / "docs" / "news-live.json").read_text(encoding="utf-8")).get("news") or [])
    except Exception:
        prev = []
    prev_ar = {
        (n.get("id") or news_fingerprint(n.get("title") or "", n.get("symbol") or "")): n
        for n in prev
        if _is_good_ar(str(n.get("title_ar") or ""), str(n.get("title") or ""))
    }
    merged: list[dict] = []
    seen_ids: set[str] = set()
    for n in list(fresh) + list(news) + prev:
        i = n.get("id") or news_fingerprint(n.get("title") or "", n.get("symbol") or "")
        if i in seen_ids:
            continue
        seen_ids.add(i)
        n["id"] = i
        if not _is_good_ar(str(n.get("title_ar") or ""), str(n.get("title") or "")):
            old = prev_ar.get(i)
            if old and _is_good_ar(str(old.get("title_ar") or ""), str(old.get("title") or "")):
                n["title_ar"] = old["title_ar"]
                if old.get("summary_ar"):
                    n["summary_ar"] = old.get("summary_ar")
        merged.append(n)
    merged.sort(key=lambda n: int(n.get("published_ts") or 0), reverse=True)
    # أسقط MARKET/رموز مزيفة من اللوحة نهائيًا (حتى لو بقيت من النسخ السابقة)
    merged = [n for n in merged if _is_real_ticker(str(n.get("symbol") or ""))]
    merged = merged[:50]
    # Translate a few missing Arabic titles each cycle (quota-safe)
    translated = ensure_news_arabic(merged, max_new=int(__import__("os").getenv("NEWS_TRANSLATE_BUDGET", "5")))
    _write_news_live(merged, fresh_n=len(burst))
    save_seen_news(seen)
    ar_n = sum(1 for n in merged if _is_good_ar(str(n.get("title_ar") or ""), str(n.get("title") or "")))
    junk_note = "بدون MARKET — رموز شركات فقط"
    print(
        f"[news] wrote news-live.json n={len(merged)} fresh={len(fresh)} "
        f"ar={ar_n} newly_translated={translated} ({junk_note}; push deferred to publish_pages)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
    fetch_stock_news_ar,
    load_seen_news,
    news_fingerprint,
    save_seen_news,
)

def run(cmd: list[str]) -> int:
    print("+", " ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT)


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
    except Exception:
        pass
    # liquid movers often in the news
    for s in ("SPY", "QQQ", "NVDA", "TSLA", "AMD", "AAPL", "META", "AMZN", "MSFT", "PLTR", "COIN", "BA"):
        syms.append(s)
    return list(dict.fromkeys(s.upper() for s in syms if s))[:28]


def _write_news_live(items: list[dict], *, fresh_n: int) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_ts": int(time.time()),
        "fresh_this_minute": fresh_n,
        "count": len(items),
        "note_ar": "أخبار الموقع فقط — محايد/إيجابي · تتحدّث كل دقيقة",
        "news": items,
    }
    raw = json.dumps(payload, ensure_ascii=False)
    for d in (ROOT / "docs", ROOT / "pages"):
        d.mkdir(parents=True, exist_ok=True)
        (d / "news-live.json").write_text(raw, encoding="utf-8")


def main() -> int:
    settings = load_settings()
    syms = _symbols(settings)
    pack = fetch_stock_news_ar(
        syms,
        limit=40,
        per_symbol=4,
        drop_negative_symbols=False,  # feed volume: drop only neg headlines
        translate_summary=False,
        include_market=True,
    )
    news = [n for n in (pack.get("news") or []) if n.get("sentiment") in ("pos", "neu")]

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

    # Rolling board: newest first, keep 40
    # Prefer previously published live file + new items
    prev: list[dict] = []
    try:
        prev = list(json.loads((ROOT / "docs" / "news-live.json").read_text(encoding="utf-8")).get("news") or [])
    except Exception:
        prev = []
    merged: list[dict] = []
    seen_ids: set[str] = set()
    for n in list(fresh) + list(news) + prev:
        i = n.get("id") or news_fingerprint(n.get("title") or "", n.get("symbol") or "")
        if i in seen_ids:
            continue
        seen_ids.add(i)
        n["id"] = i
        merged.append(n)
        if len(merged) >= 40:
            break
    _write_news_live(merged, fresh_n=len(burst))
    save_seen_news(seen)
    print(f"[news] wrote news-live.json n={len(merged)} fresh={len(fresh)}")

    run(["git", "add", "docs/news-live.json", "pages/news-live.json"])
    dirty = subprocess.call(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
    if dirty != 0:
        run(["git", "commit", "-m", "Auto-update news-live.json headlines"])
        run(["git", "push", "origin", "HEAD:main"])
    else:
        print("[news] no file changes to push")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

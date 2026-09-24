"""Fetch watchlist/gainer stock news and translate headlines to Arabic."""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yfinance as yf

from bot.cacheutil import cached_call

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "cache" / "news_ar"
UA = {"User-Agent": "Mozilla/5.0 us-daytrade-alerts"}

NEGATIVE = (
    "cut", "miss", "falls", "fall", "drop", "drops", "downgrade", "lawsuit", "probe",
    "fraud", "layoff", "layoffs", "recall", "ban", "crash", "plunge", "slump", "weak",
    "warning", "delay", "delays", "investigation", "fine", "penalty", "short",
)
POSITIVE = (
    "beat", "surge", "surges", "rally", "rallies", "upgrade", "record", "soar", "soars",
    "jump", "jumps", "gain", "gains", "strong", "raises", "boost", "wins", "approval",
    "profit", "growth", "buyback", "dividend", "deal", "partnership",
)


def _sentiment(title: str) -> str:
    low = (title or "").lower()
    if any(w in low for w in NEGATIVE):
        return "neg"
    if any(w in low for w in POSITIVE):
        return "pos"
    return "neu"


def _sentiment_ar(tag: str) -> str:
    return {"neg": "سلبي", "pos": "إيجابي", "neu": "محايد"}.get(tag, "محايد")


def _looks_arabic(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", text or ""))


def _translate_ar(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if _looks_arabic(text):
        return text
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(text.encode("utf-8")).hexdigest()
    path = CACHE_DIR / f"{key}.json"
    if path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("ar"):
                return str(cached["ar"])
        except Exception:
            pass

    ar = ""
    # 1) MyMemory free API (reliable for en→ar)
    try:
        r = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text[:450], "langpair": "en|ar"},
            headers=UA,
            timeout=18,
        )
        if r.ok:
            ar = ((r.json().get("responseData") or {}).get("translatedText") or "").strip()
            # MyMemory sometimes echoes English on quota
            if ar and ar.lower() == text.lower():
                ar = ""
    except Exception:
        ar = ""

    # 2) Google via deep_translator (optional)
    if not ar:
        try:
            from deep_translator import GoogleTranslator

            ar = GoogleTranslator(source="en", target="ar").translate(text[:450]) or ""
            time.sleep(0.25)
        except Exception:
            ar = ""

    if not ar:
        ar = text  # fallback: keep English rather than blank
    try:
        path.write_text(json.dumps({"en": text, "ar": ar}, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return ar


def _item_fields(item: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize both legacy and new Yahoo news shapes."""
    content = item.get("content") if isinstance(item.get("content"), dict) else {}
    title = (content.get("title") or item.get("title") or "").strip()
    if not title:
        return None
    summary = (content.get("summary") or content.get("description") or item.get("summary") or "").strip()
    link = ""
    for key in ("canonicalUrl", "clickThroughUrl"):
        node = content.get(key) if content else None
        if isinstance(node, dict) and node.get("url"):
            link = str(node["url"])
            break
    if not link:
        link = str(item.get("link") or item.get("url") or "")
    pub = content.get("pubDate") or content.get("displayTime") or item.get("providerPublishTime")
    published = ""
    ts = None
    if isinstance(pub, (int, float)):
        ts = int(pub)
        published = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    elif isinstance(pub, str) and pub:
        published = pub.replace("T", " ").replace("Z", " UTC")[:22]
        try:
            ts = int(datetime.fromisoformat(pub.replace("Z", "+00:00")).timestamp())
        except Exception:
            ts = None
    publisher = ""
    prov = content.get("provider") if content else None
    if isinstance(prov, dict):
        publisher = str(prov.get("displayName") or "")
    if not publisher:
        publisher = str(item.get("publisher") or "")
    return {
        "title": title,
        "summary": summary[:280],
        "url": link,
        "published": published,
        "published_ts": ts,
        "publisher": publisher,
    }


def _fetch_symbol_news(symbol: str, limit: int = 3) -> list[dict[str, Any]]:
    def _call():
        try:
            return list(yf.Ticker(symbol).news or [])
        except Exception:
            return []

    raw = cached_call(f"yfnews:{symbol}", _call, ttl=300) or []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        fields = _item_fields(item)
        if not fields:
            continue
        fields["symbol"] = symbol.upper()
        out.append(fields)
        if len(out) >= limit:
            break
    return out


def fetch_stock_news_ar(
    symbols: list[str],
    *,
    limit: int = 12,
    per_symbol: int = 2,
) -> list[dict[str, Any]]:
    """Return recent headlines with Arabic title/summary for dashboard."""
    seen_titles: set[str] = set()
    collected: list[dict[str, Any]] = []
    for sym in symbols:
        if not sym or len(collected) >= limit * 2:
            break
        for row in _fetch_symbol_news(str(sym).upper(), limit=per_symbol):
            key = re.sub(r"\s+", " ", row["title"].lower())
            if key in seen_titles:
                continue
            seen_titles.add(key)
            collected.append(row)

    # Prefer newest
    collected.sort(key=lambda x: x.get("published_ts") or 0, reverse=True)
    collected = collected[:limit]

    news: list[dict[str, Any]] = []
    for row in collected:
        title_ar = _translate_ar(row["title"])
        summary_ar = _translate_ar(row["summary"]) if row.get("summary") else ""
        tag = _sentiment(row["title"])
        news.append(
            {
                "symbol": row["symbol"],
                "title": row["title"],
                "title_ar": title_ar,
                "summary": row.get("summary") or "",
                "summary_ar": summary_ar,
                "url": row.get("url") or f"https://finance.yahoo.com/quote/{row['symbol']}/news",
                "publisher": row.get("publisher") or "",
                "published": row.get("published") or "",
                "published_ts": row.get("published_ts"),
                "sentiment": tag,
                "sentiment_ar": _sentiment_ar(tag),
            }
        )
        time.sleep(0.15)  # be kind to free translate APIs
    return news

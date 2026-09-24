"""Fetch watchlist/gainer stock news and translate headlines to Arabic.

Dashboard policy: only neutral + bullish (rise-biased) headlines.
Symbols with any recent negative headline are excluded entirely.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
import yfinance as yf

from bot.cacheutil import cached_call

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "cache" / "news_ar"
SEEN_PATH = ROOT / "data" / "news_seen.json"
UA = {"User-Agent": "Mozilla/5.0 us-daytrade-alerts"}
FINNHUB_KEY = os.getenv("FINNHUB_API_KEY", "").strip()

# Word-boundary negatives (single tokens)
NEGATIVE_WORDS = (
    "cut", "cuts", "miss", "misses", "falls", "fall", "drop", "drops", "downgrade",
    "downgraded", "lawsuit", "probe", "fraud", "layoff", "layoffs", "recall", "ban",
    "bans", "crash", "crashes", "plunge", "plunges", "slump", "slumps", "weak",
    "warning", "delay", "delays", "investigation", "fine", "penalty", "decline",
    "declines", "bearish", "selloff", "collapse", "collapses", "default", "bankruptcy",
    "concern", "concerns", "fear", "fears", "pressure", "pressured", "tariff", "tariffs",
    "lawsuit", "sued", "charges", "indicted", "scandal", "loss", "losses", "slash",
    "slashes", "sinks", "tumbles", "tumble", "worst",
)
NEGATIVE_PHRASES = (
    "sell-off", "sell off", "guidance cut", "cuts guidance", "misses estimates",
    "missed estimates", "below expectations", "class action", "sec charges",
    "price target cut", "lowers target", "market crash", "market collapse",
    "push back", "pushback", "heads lower", "turns lower", "profit warning",
)

# Bullish / expected-rise language
POSITIVE_WORDS = (
    "beat", "beats", "surge", "surges", "rally", "rallies", "upgrade", "upgraded",
    "record", "soar", "soars", "jump", "jumps", "gain", "gains", "strong", "raises",
    "boost", "boosts", "wins", "approval", "profit", "profits", "growth", "buyback",
    "dividend", "deal", "partnership", "bullish", "outperform", "outperforms",
    "climbs", "climb", "rises", "rise", "rising", "higher", "optimism", "optimistic",
    "expands", "expansion", "breakout", "breakthrough", "accelerate", "accelerates",
    "upside", "rebound", "rebounds", "recovery", "recovers",
)
POSITIVE_PHRASES = (
    "all-time high", "price target raised", "raises target", "above expectations",
    "beats estimates", "beat estimates", "raises guidance", "guidance raise",
    "strong demand", "record high", "new high", "buy rating", "overweight",
    "initiates buy", "street likes", "growth outlook", "earnings beat",
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _has_word(text: str, word: str) -> bool:
    return bool(re.search(rf"\b{re.escape(word)}\b", text))


def _sentiment(title: str, summary: str = "") -> str:
    """Classify headline: neg / pos (rise-biased) / neu."""
    blob = _norm(f"{title} {summary}")
    if not blob:
        return "neu"
    if any(p in blob for p in NEGATIVE_PHRASES) or any(_has_word(blob, w) for w in NEGATIVE_WORDS):
        return "neg"
    if any(p in blob for p in POSITIVE_PHRASES) or any(_has_word(blob, w) for w in POSITIVE_WORDS):
        return "pos"
    return "neu"


def _sentiment_ar(tag: str) -> str:
    return {"neg": "سلبي", "pos": "إيجابي — متوقع يدعم الارتفاع", "neu": "محايد"}.get(tag, "محايد")


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
    try:
        r = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text[:450], "langpair": "en|ar"},
            headers=UA,
            timeout=18,
        )
        if r.ok:
            ar = ((r.json().get("responseData") or {}).get("translatedText") or "").strip()
            if ar and ar.lower() == text.lower():
                ar = ""
    except Exception:
        ar = ""

    if not ar:
        try:
            from deep_translator import GoogleTranslator

            ar = GoogleTranslator(source="en", target="ar").translate(text[:450]) or ""
            time.sleep(0.25)
        except Exception:
            ar = ""

    if not ar:
        ar = text
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


def _fetch_symbol_news(symbol: str, limit: int = 4) -> list[dict[str, Any]]:
    def _call():
        rows: list[dict[str, Any]] = []
        # Finnhub company news (fast, many headlines)
        if FINNHUB_KEY:
            try:
                frm = (date.today() - timedelta(days=2)).isoformat()
                to = date.today().isoformat()
                r = requests.get(
                    "https://finnhub.io/api/v1/company-news",
                    params={"symbol": symbol, "from": frm, "to": to, "token": FINNHUB_KEY},
                    headers=UA,
                    timeout=15,
                )
                if r.ok:
                    for item in r.json() or []:
                        title = (item.get("headline") or "").strip()
                        if not title:
                            continue
                        ts = int(item.get("datetime") or 0) or None
                        published = (
                            datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                            if ts
                            else ""
                        )
                        rows.append(
                            {
                                "title": title,
                                "summary": (item.get("summary") or "")[:280],
                                "url": item.get("url") or f"https://finance.yahoo.com/quote/{symbol}/news",
                                "published": published,
                                "published_ts": ts,
                                "publisher": item.get("source") or "",
                                "symbol": symbol.upper(),
                                "source": "finnhub",
                            }
                        )
            except Exception:
                pass
        # Yahoo fallback / supplement
        try:
            for item in list(yf.Ticker(symbol).news or []):
                if not isinstance(item, dict):
                    continue
                fields = _item_fields(item)
                if not fields:
                    continue
                fields["symbol"] = symbol.upper()
                fields["source"] = "yahoo"
                rows.append(fields)
        except Exception:
            pass
        return rows

    raw = cached_call(f"newsmix:{symbol}", _call, ttl=45) or []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for fields in raw:
        title = (fields.get("title") or "").strip()
        key = re.sub(r"\s+", " ", title.lower())
        if not title or key in seen:
            continue
        seen.add(key)
        fields = dict(fields)
        fields["sentiment"] = _sentiment(fields["title"], fields.get("summary") or "")
        out.append(fields)
        if len(out) >= limit:
            break
    return out


def _fetch_market_news(limit: int = 30) -> list[dict[str, Any]]:
    """Finnhub general + market categories; keep market/stock-ish headlines."""
    if not FINNHUB_KEY:
        return []

    def _call():
        rows: list[dict[str, Any]] = []
        for cat in ("general", "merger", "forex", "crypto"):
            try:
                r = requests.get(
                    "https://finnhub.io/api/v1/news",
                    params={"category": cat, "token": FINNHUB_KEY},
                    headers=UA,
                    timeout=15,
                )
                if not r.ok:
                    continue
                for item in r.json() or []:
                    title = (item.get("headline") or "").strip()
                    if not title:
                        continue
                    related = str(item.get("related") or "").upper()
                    # Prefer items tied to tickers, or market/Fed/earnings language
                    blob = title.lower()
                    stockish = bool(related) or any(
                        w in blob
                        for w in (
                            "stock", "shares", "nasdaq", "dow", "s&p", "earnings", "fed",
                            "rate", "wall street", "ipo", "rally", "market", "treasury",
                            "oil", "semiconductor", "chip", "bank", "ai ",
                        )
                    )
                    if not stockish:
                        continue
                    ts = int(item.get("datetime") or 0) or None
                    sym = (related.split(",")[0].strip() if related else "MARKET") or "MARKET"
                    rows.append(
                        {
                            "title": title,
                            "summary": (item.get("summary") or "")[:280],
                            "url": item.get("url") or "",
                            "published": (
                                datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                                if ts
                                else ""
                            ),
                            "published_ts": ts,
                            "publisher": item.get("source") or "",
                            "symbol": sym[:12],
                            "source": f"finnhub:{cat}",
                            "sentiment": _sentiment(title, item.get("summary") or ""),
                        }
                    )
            except Exception:
                continue
        return rows

    rows = cached_call("fh:marketnews", _call, ttl=40) or []
    rows = [r for r in rows if r.get("sentiment") in ("pos", "neu")]
    rows.sort(key=lambda x: x.get("published_ts") or 0, reverse=True)
    return rows[:limit]


def news_fingerprint(title: str, symbol: str = "") -> str:
    base = re.sub(r"\s+", " ", (title or "").lower()).strip()
    return hashlib.sha1(f"{symbol}|{base}".encode("utf-8")).hexdigest()


_STOP = {
    "the", "a", "an", "to", "of", "in", "on", "for", "and", "or", "is", "are", "with", "as", "at",
    "by", "from", "stock", "shares", "inc", "corp", "co", "ltd", "plc", "after", "says", "say",
}


def _title_tokens(title: str) -> set[str]:
    t = _norm(title)
    t = re.sub(r"[^a-z0-9\u0600-\u06ff\s]", " ", t)
    return {w for w in t.split() if len(w) > 2 and w not in _STOP}


def _title_jaccard(a: str, b: str) -> float:
    ta, tb = _title_tokens(a), _title_tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    return inter / float(len(ta | tb))


def dedupe_near_news(rows: list[dict[str, Any]], *, thresh_same: float = 0.55, thresh_any: float = 0.82) -> list[dict[str, Any]]:
    """Drop near-duplicate headlines (same story rewritten / syndicated)."""
    kept: list[dict[str, Any]] = []
    for row in rows:
        title = row.get("title") or ""
        sym = str(row.get("symbol") or "").upper()
        dup = False
        for k in kept:
            kt = k.get("title") or ""
            ks = str(k.get("symbol") or "").upper()
            sim = _title_jaccard(title, kt)
            same_sym = bool(sym and ks and sym == ks)
            marketish = sym == "MARKET" or ks == "MARKET"
            if same_sym and sim >= thresh_same:
                dup = True
                break
            if marketish and sim >= 0.62:
                dup = True
                break
            if sim >= thresh_any:
                dup = True
                break
            na = re.sub(r"\s+", " ", _norm(title))[:48]
            nb = re.sub(r"\s+", " ", _norm(kt))[:48]
            if na and na == nb:
                dup = True
                break
        if not dup:
            kept.append(row)
    return kept


def load_seen_news() -> dict[str, float]:
    try:
        data = json.loads(SEEN_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): float(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def save_seen_news(seen: dict[str, float], keep: int = 800) -> None:
    # prune oldest
    items = sorted(seen.items(), key=lambda kv: kv[1], reverse=True)[:keep]
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(dict(items), ensure_ascii=False), encoding="utf-8")


def fetch_stock_news_ar(
    symbols: list[str],
    *,
    limit: int = 30,
    per_symbol: int = 4,
    drop_negative_symbols: bool = True,
    translate_summary: bool = False,
    include_market: bool = True,
) -> dict[str, Any]:
    """Return bullish/neutral Arabic news (+ optional negative symbol list)."""
    seen_titles: set[str] = set()
    collected: list[dict[str, Any]] = []
    negative_symbols: set[str] = set()

    for sym in symbols:
        if not sym:
            continue
        sym_u = str(sym).upper()
        rows = _fetch_symbol_news(sym_u, limit=per_symbol)
        if any(r.get("sentiment") == "neg" for r in rows):
            negative_symbols.add(sym_u)
            if drop_negative_symbols:
                continue
        for row in rows:
            key = re.sub(r"\s+", " ", row["title"].lower())
            if key in seen_titles:
                continue
            seen_titles.add(key)
            if row.get("sentiment") not in ("pos", "neu"):
                continue
            if drop_negative_symbols and sym_u in negative_symbols:
                continue
            collected.append(row)

    if include_market:
        for row in _fetch_market_news(limit=25):
            key = re.sub(r"\s+", " ", row["title"].lower())
            if key in seen_titles:
                continue
            seen_titles.add(key)
            collected.append(row)

    collected.sort(
        key=lambda x: (1 if x.get("sentiment") == "pos" else 0, x.get("published_ts") or 0),
        reverse=True,
    )
    collected = dedupe_near_news(collected)
    collected = collected[:limit]

    news: list[dict[str, Any]] = []
    for row in collected:
        title_ar = _translate_ar(row["title"])
        summary_ar = ""
        if translate_summary and row.get("summary"):
            summary_ar = _translate_ar(row["summary"])
        tag = row.get("sentiment") or _sentiment(row["title"], row.get("summary") or "")
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
                "id": news_fingerprint(row["title"], row.get("symbol") or ""),
                "source": row.get("source") or "",
            }
        )
        time.sleep(0.05)
    return {
        "news": news,
        "negative_symbols": sorted(negative_symbols),
    }

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


def _finnhub_key() -> str:
    """Read key at call-time (env may load after import)."""
    k = (os.getenv("FINNHUB_API_KEY") or "").strip().strip('"').strip("'")
    if k:
        return k
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("FINNHUB_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


FINNHUB_KEY = _finnhub_key()  # best-effort at import; _finnhub_key() used in fetches

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


def _is_good_ar(ar: str, src: str = "") -> bool:
    """True only for usable Arabic (never accept EN fallback / quota warnings)."""
    ar = (ar or "").strip()
    if not ar or not _looks_arabic(ar):
        return False
    up = ar.upper()
    if "MYMEMORY WARNING" in up or "QUOTA" in up or "TOO MANY REQUESTS" in up:
        return False
    if src and ar.strip().lower() == src.strip().lower():
        return False
    return True


def _cache_path(text: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{key}.json"


def _read_ar_cache(text: str) -> str:
    path = _cache_path(text)
    if not path.exists():
        return ""
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        ar = str(cached.get("ar") or "").strip()
        if _is_good_ar(ar, text):
            return ar
    except Exception:
        pass
    return ""


def _write_ar_cache(text: str, ar: str) -> None:
    if not _is_good_ar(ar, text):
        return
    try:
        _cache_path(text).write_text(
            json.dumps({"en": text, "ar": ar}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def purge_bad_news_ar_cache() -> int:
    """Delete poisoned cache entries that stored English (or warnings) as 'ar'."""
    if not CACHE_DIR.exists():
        return 0
    removed = 0
    for path in CACHE_DIR.glob("*.json"):
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            ar = str(cached.get("ar") or "")
            en = str(cached.get("en") or "")
            if _is_good_ar(ar, en):
                continue
            path.unlink(missing_ok=True)
            removed += 1
        except Exception:
            try:
                path.unlink(missing_ok=True)
                removed += 1
            except Exception:
                pass
    return removed


def _via_mymemory(text: str) -> str:
    email = (os.getenv("MYMEMORY_EMAIL") or "").strip()
    params = {"q": text[:450], "langpair": "en|ar"}
    if email:
        params["de"] = email
    r = requests.get(
        "https://api.mymemory.translated.net/get",
        params=params,
        headers=UA,
        timeout=5,
    )
    if not r.ok:
        return ""
    data = r.json() if r.content else {}
    if data.get("quotaFinished"):
        return ""
    ar = ((data.get("responseData") or {}).get("translatedText") or "").strip()
    if not _is_good_ar(ar, text):
        return ""
    return ar


def _via_google(text: str) -> str:
    from deep_translator import GoogleTranslator

    # auto → catches EN + occasional non-EN wires
    ar = GoogleTranslator(source="auto", target="ar").translate(text[:450]) or ""
    time.sleep(0.35)
    return ar if _is_good_ar(ar, text) else ""


def _via_argos(text: str) -> str:
    """Offline EN→AR if enabled and argostranslate + language pack are installed.

    Disabled by default: loading Torch/Argos on every vps_loop subprocess adds
    ~1–2 minutes per cycle and makes «آخر فحص» look stuck. Set NEWS_USE_ARGOS=1
    for a separate/slow translate pass when online APIs are blocked.
    """
    if (os.getenv("NEWS_USE_ARGOS") or "0").strip().lower() not in ("1", "true", "yes", "on"):
        return ""
    try:
        import argostranslate.translate  # type: ignore
    except Exception:
        return ""
    try:
        # Lazy install once if pack missing (dev / older images)
        try:
            installed = argostranslate.translate.get_installed_languages()
            has = any(getattr(l, "code", "") == "en" for l in installed) and any(
                getattr(l, "code", "") == "ar" for l in installed
            )
        except Exception:
            has = False
        if not has:
            try:
                import argostranslate.package as pkg  # type: ignore

                pkg.update_package_index()
                pkg.install_package_for_language_pair("en", "ar")
            except Exception:
                return ""
        ar = argostranslate.translate.translate(text[:450], "en", "ar") or ""
        if not _is_good_ar(ar, text):
            return ""
        # Keep common brand/ticker tokens readable
        for brand in (
            "Apple", "NVIDIA", "Nvidia", "Tesla", "Amazon", "Microsoft", "Meta",
            "Google", "Alphabet", "Netflix", "Intel", "AMD", "Oracle", "Palantir",
            "Coinbase", "Boeing", "Nasdaq", "Dow", "Fed",
        ):
            if brand.lower() in text.lower() and brand not in ar:
                # soft: leave as-is; Argos sometimes calques brand names
                pass
        return ar
    except Exception:
        return ""


def load_news_live(max_age_sec: int = 180) -> list[dict[str, Any]]:
    """Reuse docs/pages news-live.json when fresh — avoids re-translating in build_pages."""
    now = time.time()
    best: list[dict[str, Any]] = []
    best_age = 10**9
    for folder in (ROOT / "docs", ROOT / "pages"):
        path = folder / "news-live.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            rows = list(data.get("news") or [])
            if not rows:
                continue
            ts = float(data.get("generated_ts") or path.stat().st_mtime)
            age = now - ts
            if age < best_age:
                best_age = age
                best = rows
        except Exception:
            continue
    if best and best_age <= max_age_sec:
        return best
    return best if best and best_age <= max_age_sec * 2 else []


def _looks_mostly_english(text: str) -> bool:
    """Drop obvious non-English IR wires from translation/priority path."""
    t = (text or "").strip()
    if not t:
        return False
    # If already Arabic, fine
    if _looks_arabic(t):
        return True
    letters = re.findall(r"[A-Za-zÀ-ÿ]", t)
    if len(letters) < 8:
        return False
    ascii_letters = sum(1 for c in letters if c.isascii())
    ratio = ascii_letters / max(1, len(letters))
    if ratio < 0.85:
        return False
    # High-ASCII Latin without heavy diacritics ≈ English finance wire
    if ratio >= 0.98:
        return True
    low = f" {t.lower()} "
    return any(
        w in low
        for w in (
            " the ", " a ", " to ", " of ", " in ", " for ", " and ", " on ",
            " stock", " share", " company", " announces", " launches", " raises",
            " report", " earnings", " deal", " acquires", " partnership",
            " jumps ", " rises ", " surge", " after ",
        )
    ) or bool(re.search(r"\b(Inc|Corp|Ltd|PLC|CEO|EPS|AI|SEC)\b", t))


# Soft budget so free APIs survive the day (publish loop is every ~45s).
_TRANSLATE_BUDGET = {"left": int(os.getenv("NEWS_TRANSLATE_BUDGET", "12"))}
_TRANSLATE_BUDGET_TS = {"day": ""}


def _reset_budget_if_needed() -> None:
    day = time.strftime("%Y-%m-%d", time.gmtime())
    if _TRANSLATE_BUDGET_TS["day"] != day:
        _TRANSLATE_BUDGET_TS["day"] = day
        _TRANSLATE_BUDGET["left"] = int(os.getenv("NEWS_TRANSLATE_BUDGET", "12"))


def _translate_ar(text: str, *, force: bool = False) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if _looks_arabic(text):
        return text

    cached = _read_ar_cache(text)
    if cached:
        return cached

    _reset_budget_if_needed()
    if not force and _TRANSLATE_BUDGET["left"] <= 0:
        # Keep English for now; next cycles will translate when budget refreshes / cache warms
        return text

    ar = ""
    for fn in (_via_mymemory, _via_google, _via_argos):
        try:
            ar = fn(text) or ""
        except Exception:
            ar = ""
        if _is_good_ar(ar, text):
            break
        ar = ""

    if _is_good_ar(ar, text):
        if not force:
            _TRANSLATE_BUDGET["left"] = max(0, _TRANSLATE_BUDGET["left"] - 1)
        _write_ar_cache(text, ar)
        return ar

    # Do NOT cache failures — retry later when quota recovers
    return text


def ensure_news_arabic(rows: list[dict[str, Any]], *, max_new: int = 12) -> int:
    """Fill missing title_ar on news rows. Returns how many newly translated."""
    done = 0
    for row in rows:
        title = str(row.get("title") or "").strip()
        title_ar = str(row.get("title_ar") or "").strip()
        if _is_good_ar(title_ar, title):
            continue
        if not title:
            continue
        if not _looks_mostly_english(title):
            # Keep original (PL/DE/ES wires) — don't burn quota on non-English
            row["title_ar"] = title_ar or title
            continue
        if done >= max_new:
            break
        ar = _translate_ar(title, force=False)
        if _is_good_ar(ar, title):
            row["title_ar"] = ar
            done += 1
        else:
            row["title_ar"] = title_ar or title
    return done


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


_NAME_STOP = frozenset(
    {
        "inc", "corp", "ltd", "llc", "plc", "the", "and", "holdings", "company", "group",
        "ordinary", "shares", "class", "common", "stock", "new", "old",
    }
)


def _company_name_tokens(name: str) -> list[str]:
    toks = re.findall(r"[A-Za-z]{3,}", name or "")
    return [t for t in toks if t.lower() not in _NAME_STOP][:5]


def _title_matches_symbol(title: str, symbol: str, name_tokens: list[str]) -> bool:
    t = title or ""
    sym = (symbol or "").upper()
    if not t or not sym:
        return False
    if re.search(rf"\b{re.escape(sym)}\b", t, flags=re.I):
        return True
    if f"({sym})" in t.upper() or f"[{sym}]" in t.upper():
        return True
    low = t.lower()
    hits = sum(1 for tok in name_tokens[:3] if tok.lower() in low)
    # short/unique names (Veea, ReTo) → 1 token enough; longer need 2
    need = 1 if name_tokens and len(name_tokens[0]) >= 4 and len(name_tokens) <= 2 else 2
    return hits >= min(need, max(1, len(name_tokens[:3])))


def _fetch_yahoo_search_news(symbol: str, limit: int = 6) -> list[dict[str, Any]]:
    """Company news via Yahoo search, filtered by ticker/company name (no Finnhub needed)."""
    sym = (symbol or "").upper().strip()
    if not sym or not sym.isalpha() or len(sym) > 5:
        return []
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v1/finance/search",
            params={"q": sym, "quotesCount": 5, "newsCount": 15},
            headers=UA,
            timeout=18,
        )
        if not r.ok:
            return []
        js = r.json() or {}
    except Exception:
        return []
    quotes = list(js.get("quotes") or [])
    q0 = next((q for q in quotes if str(q.get("symbol") or "").upper() == sym), None)
    if not q0:
        return []
    name = str(q0.get("shortname") or q0.get("longname") or "")
    tokens = _company_name_tokens(name)
    rows: list[dict[str, Any]] = []
    for item in list(js.get("news") or []):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title or not _title_matches_symbol(title, sym, tokens):
            continue
        ts = item.get("providerPublishTime")
        published = ""
        ts_i = None
        if isinstance(ts, (int, float)) and ts:
            ts_i = int(ts)
            published = datetime.fromtimestamp(ts_i, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        rows.append(
            {
                "title": title,
                "summary": str(item.get("summary") or "")[:280],
                "url": str(item.get("link") or item.get("url") or f"https://finance.yahoo.com/quote/{sym}/news"),
                "published": published,
                "published_ts": ts_i,
                "publisher": str(item.get("publisher") or ""),
                "symbol": sym,
                "source": "yahoo_search",
                "company_name": name,
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _fetch_symbol_news(symbol: str, limit: int = 4) -> list[dict[str, Any]]:
    def _call():
        rows: list[dict[str, Any]] = []
        key = _finnhub_key()
        # Resolve company name once (for filtering generic Finnhub noise)
        name_tokens: list[str] = []
        try:
            yr = requests.get(
                "https://query1.finance.yahoo.com/v1/finance/search",
                params={"q": symbol, "quotesCount": 3, "newsCount": 0},
                headers=UA,
                timeout=12,
            )
            if yr.ok:
                for q in list((yr.json() or {}).get("quotes") or []):
                    if str(q.get("symbol") or "").upper() == symbol.upper():
                        name_tokens = _company_name_tokens(
                            str(q.get("shortname") or q.get("longname") or "")
                        )
                        break
        except Exception:
            pass
        # Finnhub company news (fast, many headlines)
        if key:
            try:
                frm = (date.today() - timedelta(days=5)).isoformat()
                to = date.today().isoformat()
                r = requests.get(
                    "https://finnhub.io/api/v1/company-news",
                    params={"symbol": symbol, "from": frm, "to": to, "token": key},
                    headers=UA,
                    timeout=15,
                )
                if r.ok:
                    for item in r.json() or []:
                        title = (item.get("headline") or "").strip()
                        if not title:
                            continue
                        # Drop generic market blurbs wrongly tagged to the symbol
                        if name_tokens and not _title_matches_symbol(title, symbol, name_tokens):
                            if not re.search(rf"\b{re.escape(symbol)}\b", title, flags=re.I):
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
        # Yahoo search first-quality (works without Finnhub; filters by company name)
        try:
            rows.extend(_fetch_yahoo_search_news(symbol, limit=max(limit, 6)))
        except Exception:
            pass
        # Prefer company-name matches: put yahoo_search ahead when sorting later via seen order
        # Reorder: yahoo_search first, then finnhub/yahoo
        rows.sort(key=lambda x: 0 if str(x.get("source") or "") == "yahoo_search" else 1)
        # Yahoo yfinance fallback / supplement
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

    raw = cached_call(f"newsmix:{symbol}:v2", _call, ttl=90) or []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for fields in raw:
        title = (fields.get("title") or "").strip()
        key = re.sub(r"\s+", " ", title.lower())
        if not title or key in seen:
            continue
        seen.add(key)
        fields = dict(fields)
        fields["symbol"] = str(fields.get("symbol") or symbol).upper()
        fields["sentiment"] = _sentiment(fields["title"], fields.get("summary") or "")
        out.append(fields)
        if len(out) >= limit:
            break
    return out


def _fetch_market_news(limit: int = 30) -> list[dict[str, Any]]:
    """Finnhub general + market categories; keep market/stock-ish headlines."""
    if not _finnhub_key():
        return []

    def _call():
        rows: list[dict[str, Any]] = []
        key = _finnhub_key()
        for cat in ("general", "merger", "forex", "crypto"):
            try:
                r = requests.get(
                    "https://finnhub.io/api/v1/news",
                    params={"category": cat, "token": key},
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

    # Stock Titan live feed + underlying wires (GlobeNewswire / PR Newswire / SEC)
    try:
        from bot.stocktitan_news import fetch_stocktitan_style_news

        for row in fetch_stocktitan_style_news(limit=max(40, limit)):
            title = (row.get("title") or "").strip()
            if not title:
                continue
            key = re.sub(r"\s+", " ", title.lower())
            if key in seen_titles:
                continue
            if (row.get("sentiment") or "neu") not in ("pos", "neu"):
                continue
            seen_titles.add(key)
            collected.append(row)
    except Exception:
        pass

    def _rank_key(x: dict[str, Any]) -> tuple:
        src = str(x.get("source") or "")
        official = bool(x.get("officialish") or src.startswith(("stocktitan", "wire:")))
        return (1 if official else 0, 1 if x.get("sentiment") == "pos" else 0, x.get("published_ts") or 0)

    collected.sort(key=_rank_key, reverse=True)
    collected = dedupe_near_news(collected)
    collected = collected[:limit]

    news: list[dict[str, Any]] = []
    for row in collected:
        # Prefer cache; budget limits live API calls so free quotas last the day
        title_ar = _translate_ar(row["title"])
        summary_ar = ""
        if translate_summary and row.get("summary"):
            summary_ar = _translate_ar(row["summary"])
        tag = row.get("sentiment") or _sentiment(row["title"], row.get("summary") or "")
        from bot.catalyst_scan import enrich_news_item

        item = {
            "symbol": row["symbol"],
            "title": row["title"],
            "title_ar": title_ar if _is_good_ar(title_ar, row["title"]) else row["title"],
            "summary": row.get("summary") or "",
            "summary_ar": summary_ar if _is_good_ar(summary_ar, row.get("summary") or "") else "",
            "url": row.get("url") or f"https://finance.yahoo.com/quote/{row['symbol']}/news",
            "publisher": row.get("publisher") or "",
            "published": row.get("published") or "",
            "published_ts": row.get("published_ts"),
            "sentiment": tag,
            "sentiment_ar": _sentiment_ar(tag),
            "id": news_fingerprint(row["title"], row.get("symbol") or ""),
            "source": row.get("source") or "",
            "officialish": bool(row.get("officialish")),
        }
        news.append(enrich_news_item(item))
    from bot.catalyst_scan import rank_catalyst_news

    news = rank_catalyst_news(news, limit=limit)
    return {
        "news": news,
        "negative_symbols": sorted(negative_symbols),
    }

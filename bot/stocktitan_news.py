"""Pull official press-release news the StockTitan way.

Stock Titan itself aggregates company wire releases (GlobeNewswire, PR Newswire,
Business Wire, AccessWire, SEC). We:
  1) scrape Stock Titan live feed via Jina reader (site blocks direct bots),
  2) also pull the same wire RSS/Atom feeds directly as a second source.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import unquote

import requests

from bot.cacheutil import cached_call
from bot.news_ar import _sentiment, news_fingerprint

UA = {
    "User-Agent": "Mozilla/5.0 (compatible; us-daytrade-alerts/1.0; +https://github.com/Aied12/us-daytrade-alerts)",
    "Accept": "text/plain, application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
}

# Stock Titan live + topic pages (official PR feed style)
STOCKTITAN_PAGES = (
    "https://www.stocktitan.net/news/live.html",
    "https://www.stocktitan.net/news/",
    "https://www.stocktitan.net/news/earnings.html",
    "https://www.stocktitan.net/news/fda-approvals.html",
    "https://www.stocktitan.net/news/clinical-trials.html",
    "https://www.stocktitan.net/news/acquisitions.html",
    "https://www.stocktitan.net/news/offerings.html",
)

# Same wire families Stock Titan prioritizes
WIRE_FEEDS = (
    (
        "globenewswire",
        "https://rss.globenewswire.com/RssFeed/orgclass/1/feedTitle/GlobeNewswire%20-%20Newsroom",
    ),
    (
        "globenewswire",
        "https://www.globenewswire.com/RssFeed/subjectcode/13/feedTitle/GlobeNewswire%20-%20Earnings",
    ),
    (
        "prnewswire",
        "https://www.prnewswire.com/rss/news-releases-list.rss",
    ),
    (
        "sec",
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&dateb=&owner=include&count=40&output=atom",
    ),
)

_TICKER_IN_TITLE = re.compile(
    r"(?:\(|\[|\bNasdaq[:\s-]*|\bNYSE[:\s-]*|\bOTC[:\s-]*)([A-Z]{1,5})\b|(?:NASDAQ|NYSE|NYSEAMERICAN|OTCQB|OTCQX)[:\s]+([A-Z]{1,5})\b"
)
_ST_BLOCK = re.compile(
    r"(?P<when>\d{2}/\d{2}/\d{4}\s+\d{1,2}:\d{2}\s+[AP]M)\s*\n"
    r"\[(?P<sym>[A-Z]{1,5})\s*:\s*(?P<ex>[^\]]+)\]\((?P<sym_url>[^)]+)\)\s*\n"
    r"\[(?P<title>[^\]]+)\]\((?P<url>[^)]+)\)",
    re.MULTILINE,
)
_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")


def _parse_st_when(s: str) -> tuple[str, int | None]:
    try:
        dt = datetime.strptime(s.strip(), "%m/%d/%Y %I:%M %p").replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC"), int(dt.timestamp())
    except Exception:
        return s, None


def _jina_get(url: str, *, timeout: int = 45) -> str:
    jina = f"https://r.jina.ai/{url}"
    r = requests.get(jina, headers=UA, timeout=timeout)
    if not r.ok:
        return ""
    return r.text or ""


def _fetch_stocktitan_page(url: str, *, limit: int = 40) -> list[dict[str, Any]]:
    def _call():
        text = _jina_get(url)
        if not text:
            return []
        rows: list[dict[str, Any]] = []
        for m in _ST_BLOCK.finditer(text):
            title = (m.group("title") or "").strip()
            sym = (m.group("sym") or "").upper()
            link = (m.group("url") or "").strip()
            when_s, ts = _parse_st_when(m.group("when") or "")
            if not title or not sym:
                continue
            rows.append(
                {
                    "symbol": sym,
                    "title": title,
                    "summary": "",
                    "url": link or m.group("sym_url") or f"https://www.stocktitan.net/news/{sym}/",
                    "published": when_s,
                    "published_ts": ts,
                    "publisher": "Stock Titan",
                    "source": "stocktitan",
                    "exchange": (m.group("ex") or "").strip(),
                    "officialish": True,
                    "sentiment": _sentiment(title, ""),
                }
            )
            if len(rows) >= limit:
                break
        # Fallback: any /news/TICKER/article links
        if len(rows) < 8:
            for title, link in _MD_LINK.findall(text):
                mm = re.search(r"stocktitan\.net/news/([A-Z]{1,5})/", link)
                if not mm:
                    continue
                sym = mm.group(1).upper()
                if title.upper().startswith(sym + " :") or len(title) < 18:
                    continue
                rows.append(
                    {
                        "symbol": sym,
                        "title": title.strip(),
                        "summary": "",
                        "url": link,
                        "published": "",
                        "published_ts": int(time.time()),
                        "publisher": "Stock Titan",
                        "source": "stocktitan",
                        "officialish": True,
                        "sentiment": _sentiment(title, ""),
                    }
                )
                if len(rows) >= limit:
                    break
        return rows

    return cached_call(f"stnews:{url}", _call, ttl=90) or []


def fetch_stocktitan_live(*, limit: int = 50) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in STOCKTITAN_PAGES:
        for row in _fetch_stocktitan_page(page, limit=limit):
            fp = news_fingerprint(row.get("title") or "", row.get("symbol") or "")
            if fp in seen:
                continue
            seen.add(fp)
            row["id"] = fp
            out.append(row)
            if len(out) >= limit:
                return out
    return out


def _extract_ticker(title: str, link: str = "", summary: str = "") -> str:
    blob = f"{title} {summary} {unquote(link)}"
    m = _TICKER_IN_TITLE.search(blob)
    if m:
        return (m.group(1) or m.group(2) or "").upper()
    m2 = re.search(r"\(([A-Z]{1,5})\)\s*$", title.strip())
    if m2:
        return m2.group(1).upper()
    # SEC atom titles often: "8-K - Company Name (0001234567) (Filer)"
    m3 = re.search(r"/Archives/edgar/data/\d+/[^/]+/([A-Z0-9-]+)", link)
    if m3 and re.fullmatch(r"[A-Z]{1,5}", m3.group(1) or ""):
        return m3.group(1).upper()
    return "MARKET"


def _rss_pub_ts(item: ET.Element) -> tuple[str, int | None]:
    raw = (
        item.findtext("pubDate")
        or item.findtext("{http://purl.org/dc/elements/1.1/}date")
        or item.findtext("{http://www.w3.org/2005/Atom}updated")
        or item.findtext("{http://www.w3.org/2005/Atom}published")
        or ""
    )
    if not raw:
        return "", None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), int(dt.timestamp())
    except Exception:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), int(dt.timestamp())
        except Exception:
            return raw[:22], None


def _parse_rss(xml_text: bytes | str, *, publisher: str, limit: int = 40) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    # RSS 2.0
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        link = (item.findtext("link") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
        desc = re.sub(r"\s+", " ", desc).strip()[:280]
        published, ts = _rss_pub_ts(item)
        sym = _extract_ticker(title, link, desc)
        rows.append(
            {
                "symbol": sym,
                "title": title,
                "summary": desc,
                "url": link,
                "published": published,
                "published_ts": ts,
                "publisher": publisher,
                "source": f"wire:{publisher.lower().replace(' ', '')}",
                "officialish": True,
                "sentiment": _sentiment(title, desc),
            }
        )
        if len(rows) >= limit:
            break
    if rows:
        return rows
    # Atom (SEC)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for item in root.findall(".//{http://www.w3.org/2005/Atom}entry") or root.findall(".//a:entry", ns):
        title = (item.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
        if not title:
            continue
        link_el = item.find("{http://www.w3.org/2005/Atom}link")
        link = ""
        if link_el is not None:
            link = link_el.attrib.get("href") or ""
        summary = (item.findtext("{http://www.w3.org/2005/Atom}summary") or "")[:280]
        published, ts = _rss_pub_ts(item)
        # Prefer company tickers from title like "COMPANY NAME (TICKER)"
        sym = _extract_ticker(title, link, summary)
        rows.append(
            {
                "symbol": sym if sym != "MARKET" else "SEC",
                "title": title,
                "summary": re.sub(r"\s+", " ", summary).strip(),
                "url": link or "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent",
                "published": published,
                "published_ts": ts,
                "publisher": "SEC EDGAR",
                "source": "wire:sec",
                "officialish": True,
                "sentiment": _sentiment(title, summary),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _fetch_wire_feed(name: str, url: str, *, limit: int = 35) -> list[dict[str, Any]]:
    def _call():
        try:
            r = requests.get(url, headers=UA, timeout=25)
            if not r.ok:
                return []
            pub = {
                "globenewswire": "GlobeNewswire",
                "prnewswire": "PR Newswire",
                "sec": "SEC EDGAR",
            }.get(name, name)
            return _parse_rss(r.content, publisher=pub, limit=limit)
        except Exception:
            return []

    return cached_call(f"wire:{name}:{url[-48:]}", _call, ttl=120) or []


def fetch_wire_releases(*, limit: int = 50) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, url in WIRE_FEEDS:
        for row in _fetch_wire_feed(name, url, limit=limit):
            fp = news_fingerprint(row.get("title") or "", row.get("symbol") or "")
            if fp in seen:
                continue
            seen.add(fp)
            row["id"] = fp
            out.append(row)
            if len(out) >= limit:
                return out
    out.sort(key=lambda x: x.get("published_ts") or 0, reverse=True)
    return out[:limit]


def fetch_stocktitan_style_news(*, limit: int = 60) -> list[dict[str, Any]]:
    """Combine Stock Titan feed + underlying wire sources."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in list(fetch_stocktitan_live(limit=limit)) + list(fetch_wire_releases(limit=limit)):
        if (row.get("sentiment") or "neu") == "neg":
            continue
        fp = row.get("id") or news_fingerprint(row.get("title") or "", row.get("symbol") or "")
        if fp in seen:
            continue
        seen.add(fp)
        row = dict(row)
        row["id"] = fp
        merged.append(row)
        if len(merged) >= limit:
            break
    merged.sort(key=lambda x: x.get("published_ts") or 0, reverse=True)
    return merged[:limit]

"""StockTitan-style catalyst + momentum scan (impact, tags, news↔move)."""

from __future__ import annotations

import re
from typing import Any

from bot.liquidity import liquidity_dict
from bot.market_data import QuoteSnapshot

# Official-ish wire sources StockTitan prioritizes over commentary
OFFICIAL_PUBLISHERS = (
    "business wire",
    "globenewswire",
    "pr newswire",
    "prnewswire",
    "accesswire",
    "newsfile",
    "sec",
    "edgar",
    "company",
    "press release",
)

# Catalyst taxonomy → Arabic label + base impact
CATALYST_RULES: list[tuple[str, str, int, tuple[str, ...]]] = [
    ("fda", "FDA / دواء", 5, ("fda", "pdufa", "drug approval", "complete response", "crl", "bla ", "nda ")),
    ("clinical", "تجربة سريرية", 4, ("phase 1", "phase 2", "phase 3", "clinical trial", "trial data", "topline", "endpoint")),
    ("earnings", "أرباح", 4, ("earnings", "eps", "quarterly results", "q1 ", "q2 ", "q3 ", "q4 ", "fiscal")),
    ("guidance", "توجيهات", 4, ("guidance", "outlook", "raises guidance", "lowers guidance", "forecast")),
    ("mna", "اندماج/استحواذ", 5, ("acquire", "acquisition", "merger", "buyout", "takeover", "definitive agreement")),
    ("partnership", "شراكة/عقد", 3, ("partnership", "collaborate", "collaboration", "agreement with", "contract", "award", "wins contract")),
    ("offering", "طرح/تخفيف", 3, ("offering", "public offering", "atm offering", "dilution", "registered direct", "private placement")),
    ("buyback", "إعادة شراء", 3, ("buyback", "repurchase", "share repurchase")),
    ("dividend", "توزيعات", 2, ("dividend", "special dividend")),
    ("ipo", "طرح عام", 4, ("ipo", "begins trading", "prices offering")),
    ("split", "تجزئة", 2, ("stock split", "reverse split", "split")),
    ("insider", "مطلعون", 3, ("form 4", "insider", "13d", "13g", "beneficial owner")),
    ("sec", "إفصاح SEC", 3, ("8-k", "10-k", "10-q", "sec filing", "form 8")),
    ("upgrade", "ترقية محلل", 2, ("upgrade", "initiates", "price target raised", "overweight", "outperform")),
]

MOMENTUM_PCT = 4.0  # StockTitan Argus-style significant move
MOMENTUM_PCT_EARLY = 2.5  # softer early / quiet tape
MIN_DOLLAR_MOMENTUM = 8_000_000


def _blob(title: str, summary: str = "") -> str:
    return re.sub(r"\s+", " ", f"{title or ''} {summary or ''}".lower()).strip()


def detect_catalysts(title: str, summary: str = "") -> list[dict[str, Any]]:
    blob = _blob(title, summary)
    tags: list[dict[str, Any]] = []
    for key, ar, impact, words in CATALYST_RULES:
        if any(w in blob for w in words):
            tags.append({"key": key, "ar": ar, "base_impact": impact})
    return tags


def is_officialish(publisher: str, title: str = "", source: str = "") -> bool:
    blob = f"{publisher or ''} {title or ''} {source or ''}".lower()
    return any(p in blob for p in OFFICIAL_PUBLISHERS)


def sentiment_score_1_5(tag: str) -> int:
    """Map coarse sentiment → 1–5 tone score (StockTitan-like)."""
    return {"pos": 4, "neu": 3, "neg": 2}.get(tag or "neu", 3)


def impact_score_1_5(
    *,
    title: str,
    summary: str = "",
    sentiment: str = "neu",
    change_pct: float | None = None,
    publisher: str = "",
    source: str = "",
) -> tuple[int, list[dict[str, Any]], str]:
    """
    Estimate short-term move potential 1–5 (direction-agnostic impact).
    Returns (impact, catalyst_tags, reason_ar).
    """
    tags = detect_catalysts(title, summary)
    base = max((t["base_impact"] for t in tags), default=2)
    blob = _blob(title, summary)

    # Language amplifiers
    if any(w in blob for w in ("breakthrough", "accelerated approval", "all-time high", "record revenue")):
        base = max(base, 4)
    if any(w in blob for w in ("bankruptcy", "going concern", "halt", "delist", "fraud")):
        base = max(base, 5)

    if is_officialish(publisher, title, source):
        base = min(5, base + 1)
    elif any(w in blob for w in ("analyst", "rumor", "says source", " reportedly")):
        base = max(1, base - 1)

    if change_pct is not None:
        mag = abs(float(change_pct))
        if mag >= 8:
            base = max(base, 5)
        elif mag >= 4:
            base = max(base, 4)
        elif mag >= 2:
            base = max(base, 3)

    if sentiment == "pos" and base <= 3 and tags:
        base = min(5, base + 1)

    impact = int(max(1, min(5, base)))
    if tags:
        reason = " · ".join(t["ar"] for t in tags[:2])
    elif abs(float(change_pct or 0)) >= 4:
        reason = "تحرك سعري قوي بدون محفز واضح بعد"
    else:
        reason = "خبر عام / تأثير محدود"
    return impact, tags, reason


def enrich_news_item(item: dict[str, Any], *, change_pct: float | None = None) -> dict[str, Any]:
    """Attach StockTitan-like scoring fields onto a news dict."""
    out = dict(item)
    impact, tags, reason = impact_score_1_5(
        title=str(out.get("title") or ""),
        summary=str(out.get("summary") or ""),
        sentiment=str(out.get("sentiment") or "neu"),
        change_pct=change_pct,
        publisher=str(out.get("publisher") or ""),
        source=str(out.get("source") or ""),
    )
    sent = str(out.get("sentiment") or "neu")
    out["impact"] = impact
    out["impact_ar"] = f"تأثير {impact}/5"
    out["sentiment_score"] = sentiment_score_1_5(sent)
    out["catalysts"] = tags
    out["catalyst_keys"] = [t["key"] for t in tags]
    out["catalyst_ar"] = [t["ar"] for t in tags]
    out["impact_reason_ar"] = reason
    out["officialish"] = is_officialish(
        str(out.get("publisher") or ""),
        str(out.get("title") or ""),
        str(out.get("source") or ""),
    )
    return out


def _news_by_symbol(news: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by: dict[str, list[dict[str, Any]]] = {}
    for n in news or []:
        sym = str(n.get("symbol") or "").upper()
        if not sym or sym == "MARKET":
            continue
        by.setdefault(sym, []).append(n)
    for sym, rows in by.items():
        rows.sort(key=lambda x: (int(x.get("impact") or 0), x.get("published_ts") or 0), reverse=True)
    return by


def build_momentum_scanner(
    *,
    snaps: list[QuoteSnapshot],
    gainers: list[dict[str, Any]] | None = None,
    news: list[dict[str, Any]] | None = None,
    phase: str = "regular",
    limit: int = 12,
) -> list[dict[str, Any]]:
    """
    Argus-style momentum scanner: significant % move + liquidity,
    with the latest catalyst/news attached beside the spike.
    """
    floor = MOMENTUM_PCT_EARLY if phase in ("pre", "post") else MOMENTUM_PCT
    by_news = _news_by_symbol(news or [])
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _push(
        symbol: str,
        *,
        last: float,
        change_pct: float,
        dollar: float = 0.0,
        dollar_label: str = "",
        rvol: float | None = None,
        grade_ar: str = "",
        name: str = "",
    ) -> None:
        sym = symbol.upper()
        if not sym or sym in seen:
            return
        if abs(change_pct) < floor:
            return
        if dollar > 0 and dollar < MIN_DOLLAR_MOMENTUM and abs(change_pct) < floor + 2:
            return
        related = by_news.get(sym) or []
        top = related[0] if related else None
        if top and top.get("impact") is None:
            top = enrich_news_item(top, change_pct=change_pct)
        impact = int((top or {}).get("impact") or (5 if abs(change_pct) >= 8 else 4 if abs(change_pct) >= 4 else 3))
        rows.append(
            {
                "symbol": sym,
                "name": name,
                "last": round(float(last), 2),
                "change_pct": round(float(change_pct), 2),
                "dollar_volume": dollar,
                "dollar_volume_label": dollar_label or "",
                "rvol": rvol,
                "liq_grade_ar": grade_ar,
                "impact": impact,
                "impact_ar": f"تأثير {impact}/5",
                "has_news": bool(top),
                "news": top,
                "news_title_ar": (top or {}).get("title_ar") or (top or {}).get("title") or "",
                "news_url": (top or {}).get("url") or "",
                "catalyst_ar": (top or {}).get("catalyst_ar") or [],
                "alert_ar": (
                    f"زخم {change_pct:+.1f}% · {(top or {}).get('impact_reason_ar') or 'تحرك حجمي'}"
                    if top
                    else f"زخم {change_pct:+.1f}% · لا خبر رسمي مرفق بعد"
                ),
                "tv_url": f"https://www.tradingview.com/chart/?symbol={sym}",
            }
        )
        seen.add(sym)

    for g in gainers or []:
        _push(
            str(g.get("symbol") or ""),
            last=float(g.get("last") or 0),
            change_pct=float(g.get("change_pct") or 0),
            dollar=float(g.get("dollar_volume") or 0),
            dollar_label=str(g.get("dollar_volume_label") or ""),
            name=str(g.get("name") or ""),
        )

    for snap in snaps or []:
        liq = liquidity_dict(snap)
        _push(
            snap.symbol,
            last=float(snap.last),
            change_pct=float(snap.change_pct),
            dollar=float(liq.get("dollar_volume") or 0),
            dollar_label=str(liq.get("dollar_volume_label") or ""),
            rvol=float(liq.get("rvol_pace") or liq.get("rvol") or 0),
            grade_ar=str(liq.get("grade_ar") or ""),
        )

    rows.sort(
        key=lambda r: (
            1 if r.get("has_news") else 0,
            int(r.get("impact") or 0),
            abs(float(r.get("change_pct") or 0)),
            float(r.get("dollar_volume") or 0),
        ),
        reverse=True,
    )
    return rows[:limit]


def rank_catalyst_news(news: list[dict[str, Any]], *, limit: int = 30) -> list[dict[str, Any]]:
    """Sort feed like StockTitan: impact first, then freshness, prefer official."""
    enriched = [enrich_news_item(n) if n.get("impact") is None else n for n in (news or [])]
    enriched.sort(
        key=lambda n: (
            int(n.get("impact") or 0),
            1 if n.get("officialish") else 0,
            1 if n.get("sentiment") == "pos" else 0,
            n.get("published_ts") or 0,
        ),
        reverse=True,
    )
    return enriched[:limit]

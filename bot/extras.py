from __future__ import annotations

"""Extended market intel: premarket, after-hours, calendars, options, ETFs, style."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from bot.config import Settings
from bot.market_data import fetch_history, scan_watchlist

NY = ZoneInfo("America/New_York")
RIYADH = ZoneInfo("Asia/Riyadh")

SECTOR_ETFS = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLY": "Consumer Disc.",
    "XLP": "Consumer Staples",
    "XLV": "Health Care",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
    "XLC": "Communication",
    "SMH": "Semiconductors",
}

# 64 Growth vs Value tagging for watchlist names
GROWTH_NAMES = {
    "NVDA", "TSLA", "AMD", "META", "NFLX", "CRM", "PLTR", "COIN", "UBER", "AVGO",
}
VALUE_NAMES = {
    "JPM", "BAC", "XOM", "CVX", "COST", "DIS", "BA", "AAPL", "MSFT", "GOOGL", "AMZN",
}

# 54 Approximate FOMC / key Fed dates 2026 (update yearly)
FOMC_DATES_2026 = [
    date(2026, 1, 28),
    date(2026, 3, 18),
    date(2026, 4, 29),
    date(2026, 6, 17),
    date(2026, 7, 29),
    date(2026, 9, 16),
    date(2026, 11, 4),
    date(2026, 12, 16),
]

NEGATIVE_NEWS = (
    "fraud", "lawsuit", "probe", "investigation", "downgrade", "bankruptcy",
    "sec ", "recall", "layoff", "misses", "plunge", "crash", "default", "halts",
)
POSITIVE_NEWS = (
    "upgrade", "beats", "surge", "record", "approval", "buyback", "raises guidance",
)


@dataclass
class Mover:
    symbol: str
    last: float
    change_pct: float
    volume: float
    note: str = ""


def _session_movers(symbols: list[str], *, prepost: bool, hours: int = 8) -> list[Mover]:
    """Use intraday bars with prepost to approximate extended-hours movers."""
    movers: list[Mover] = []
    for sym in symbols:
        try:
            df = yf.download(
                sym,
                period="5d",
                interval="5m",
                prepost=prepost,
                progress=False,
                auto_adjust=True,
                threads=False,
            )
            if df is None or df.empty:
                # fallback: daily gap as proxy
                daily = fetch_history(sym, period="5d", interval="1d")
                if daily.empty or len(daily) < 2:
                    continue
                last = float(daily["Close"].iloc[-1])
                prev = float(daily["Close"].iloc[-2])
                chg = ((last / prev) - 1) * 100
                movers.append(Mover(sym, last, chg, float(daily["Volume"].iloc[-1]), "تقريبي يومي"))
                continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] for c in df.columns]
            df = df.dropna()
            if len(df) < 3:
                continue
            # compare last bar vs prior regular close proxy (first bar of last day open-ish)
            last = float(df["Close"].iloc[-1])
            # use close from ~1 trading day ago index
            ref = float(df["Close"].iloc[max(0, len(df) - 78)])  # ~6.5h of 5m bars
            chg = ((last / ref) - 1) * 100 if ref else 0.0
            vol = float(df["Volume"].tail(12).sum())
            movers.append(Mover(sym.upper(), last, chg, vol, "extended" if prepost else "regular"))
        except Exception:
            continue
    movers.sort(key=lambda m: abs(m.change_pct), reverse=True)
    return movers


def format_premarket_hotlist(settings: Settings) -> str:
    """51 Premarket movers."""
    symbols = [s for s in settings.watchlist if s not in ("SPY", "QQQ", "IWM")]
    movers = _session_movers(symbols, prepost=True)[:10]
    now = datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M")
    lines = [
        "🌅 Premarket — قائمة ساخنة",
        f"⏰ {now} السعودية",
        "",
    ]
    if not movers:
        lines.append("لا بيانات premaket الآن.")
        return "\n".join(lines)
    for i, m in enumerate(movers[:8], 1):
        style = style_tag(m.symbol)
        lines.append(
            f"{i}) {m.symbol} {style} {m.change_pct:+.2f}% @ ${m.last:.2f}"
        )
    lines.append("")
    lines.append("تعليمي فقط — تحركات ما قبل الافتتاح متقلبة.")
    return "\n".join(lines)


def format_after_hours(settings: Settings) -> str:
    """52 After-hours summary."""
    symbols = [s for s in settings.watchlist if s not in ("SPY", "QQQ", "IWM")]
    movers = _session_movers(symbols, prepost=True)[:10]
    now = datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M")
    lines = [
        "🌙 After-hours — ملخص بعد الجلسة",
        f"⏰ {now} السعودية",
        "",
    ]
    up = [m for m in movers if m.change_pct >= 0.8][:5]
    down = [m for m in movers if m.change_pct <= -0.8][:5]
    lines.append("الأقوى بعد الإغلاق:")
    if up:
        for m in up:
            lines.append(f"  🟢 {m.symbol} {m.change_pct:+.2f}%")
    else:
        lines.append("  لا صعود بارز")
    lines.append("الأضعف:")
    if down:
        for m in down:
            lines.append(f"  🔴 {m.symbol} {m.change_pct:+.2f}%")
    else:
        lines.append("  لا هبوط بارز")
    lines.append("")
    lines.append("بعد الإغلاق سيولة أقل — لا تتعجل قرارات.")
    return "\n".join(lines)


def style_tag(symbol: str) -> str:
    """64 Growth vs Value badge."""
    u = symbol.upper()
    if u in GROWTH_NAMES:
        return "🌱نمو"
    if u in VALUE_NAMES:
        return "🏦قيمة"
    return ""


def format_style_board(settings: Settings) -> str:
    """64 Growth vs Value board from watchlist performance."""
    snaps = scan_watchlist(settings.watchlist)
    growth = [s for s in snaps if s.symbol in GROWTH_NAMES]
    value = [s for s in snaps if s.symbol in VALUE_NAMES]
    g_avg = sum(s.change_pct for s in growth) / len(growth) if growth else 0.0
    v_avg = sum(s.change_pct for s in value) / len(value) if value else 0.0
    lines = [
        "🌱🏦 نمو vs قيمة",
        f"متوسط النمو: {g_avg:+.2f}% ({len(growth)} أسهم)",
        f"متوسط القيمة: {v_avg:+.2f}% ({len(value)} أسهم)",
        "",
        "أبرز النمو:",
    ]
    for s in sorted(growth, key=lambda x: x.change_pct, reverse=True)[:5]:
        lines.append(f"  • {s.symbol} {s.change_pct:+.2f}%")
    lines.append("أبرز القيمة:")
    for s in sorted(value, key=lambda x: x.change_pct, reverse=True)[:5]:
        lines.append(f"  • {s.symbol} {s.change_pct:+.2f}%")
    leader = "النمو" if g_avg > v_avg else "القيمة" if v_avg > g_avg else "تعادل"
    lines.append("")
    lines.append(f"اليوم يتقدّم أسلوب: {leader}")
    return "\n".join(lines)


def format_earnings_calendar(settings: Settings, within_days: int = 14) -> str:
    """53 Earnings calendar for watchlist."""
    lines = [f"📅 تقويم الأرباح — خلال {within_days} يوم", ""]
    rows: list[tuple[int, str]] = []
    for sym in settings.watchlist:
        if sym in ("SPY", "QQQ", "IWM"):
            continue
        try:
            t = yf.Ticker(sym)
            ed = None
            try:
                ed = t.get_earnings_dates(limit=4)
            except Exception:
                continue
            if ed is None or getattr(ed, "empty", True):
                continue
            today = pd.Timestamp.utcnow().tz_localize(None).normalize()
            for d in pd.to_datetime(ed.index):
                dd = pd.Timestamp(d).tz_localize(None).normalize()
                delta = (dd - today).days
                if 0 <= delta <= within_days:
                    rows.append((delta, f"• {sym}: بعد {delta} يوم ({dd.date()}) {style_tag(sym)}"))
                    break
        except Exception:
            continue
    rows.sort()
    if not rows:
        lines.append("لا أرباح قريبة ضمن القائمة الحالية.")
    else:
        lines.extend(r[1] for r in rows)
    lines.append("")
    lines.append("قرب الأرباح = تقلّب أعلى — خفّض الحجم أو تجنّب.")
    return "\n".join(lines)


def format_fed_calendar() -> str:
    """54 Fed / FOMC calendar."""
    today = datetime.now(NY).date()
    upcoming = [d for d in FOMC_DATES_2026 if d >= today][:4]
    past = [d for d in FOMC_DATES_2026 if d < today][-2:]
    lines = ["🏛 تقويم الفيدرالي / الفائدة (FOMC 2026)", ""]
    if past:
        lines.append("آخر اجتماعات:")
        for d in past:
            lines.append(f"  • {d.isoformat()}")
    lines.append("القادمة:")
    if not upcoming:
        lines.append("  لا تواريخ متبقية مخزّنة لسنة 2026")
    else:
        for d in upcoming:
            delta = (d - today).days
            flag = "⚠️ قريب" if delta <= 3 else ""
            lines.append(f"  • {d.isoformat()} (بعد {delta} يوم) {flag}")
    lines.append("")
    lines.append("يوم القرار ويوم ما بعده: سيولة وتذبذب أعلى من المعتاد.")
    return "\n".join(lines)


def format_stock_news(settings: Settings, limit_per: int = 2) -> str:
    """55 Urgent news linked to watchlist symbols (Arabic titles)."""
    try:
        from bot.news_ar import fetch_stock_news_ar

        rows = fetch_stock_news_ar(list(settings.watchlist or []), limit=12, per_symbol=limit_per)
    except Exception:
        rows = []
    lines = ["📰 أخبار عاجلة مرتبطة بالقائمة", ""]
    if not rows:
        lines.append("لا عناوين متاحة الآن.")
    else:
        for n in rows:
            tag = {"pos": "🟢", "neg": "🔴"}.get(n.get("sentiment") or "", "⚪")
            title = n.get("title_ar") or n.get("title") or ""
            lines.append(f"{tag} {n.get('symbol')}: {title[:140]}")
    lines.append("")
    lines.append("العناوين مترجمة آلياً — تحقق من المصدر قبل أي قرار.")
    return "\n".join(lines)


def _option_unusual(symbol: str) -> Optional[str]:
    """56 Rough unusual options: high volume vs open interest on near expiry."""
    try:
        t = yf.Ticker(symbol)
        exps = t.options
        if not exps:
            return None
        chain = t.option_chain(exps[0])
        calls = chain.calls
        puts = chain.puts
        if calls is None or calls.empty:
            return None
        calls = calls.copy()
        puts = puts.copy() if puts is not None else pd.DataFrame()
        # Prefer volume; if OI missing/zero, still flag high absolute volume
        def _score(df: pd.DataFrame) -> pd.DataFrame:
            vol = df["volume"].fillna(0)
            oi = df["openInterest"].fillna(0).replace(0, pd.NA)
            df = df.copy()
            df["score"] = vol / oi.fillna(vol.clip(lower=1))
            df.loc[oi.isna() | (oi == 0), "score"] = vol
            return df

        calls = _score(calls)
        top_c = calls.sort_values(["volume", "score"], ascending=False).head(1)
        msg = None
        if not top_c.empty and float(top_c["volume"].iloc[0] or 0) >= 2000:
            row = top_c.iloc[0]
            oi_v = int(row.get("openInterest") or 0)
            msg = (
                f"{symbol} CALL {row.get('strike')} vol={int(row.get('volume') or 0)} OI={oi_v}"
            )
        if puts is not None and not puts.empty:
            puts = _score(puts)
            top_p = puts.sort_values(["volume", "score"], ascending=False).head(1)
            if not top_p.empty and float(top_p["volume"].iloc[0] or 0) >= 2000:
                row = top_p.iloc[0]
                oi_v = int(row.get("openInterest") or 0)
                put_msg = (
                    f"{symbol} PUT {row.get('strike')} vol={int(row.get('volume') or 0)} OI={oi_v}"
                )
                msg = f"{msg} | {put_msg}" if msg else put_msg
        return msg
    except Exception:
        return None


def format_options_unusual(settings: Settings) -> str:
    """56 Options unusual volume scan (subset for speed)."""
    lines = ["📊 خيارات — حجم غير طبيعي (تقريبي)", ""]
    # Focus liquid names
    focus = [s for s in settings.watchlist if s in {
        "AAPL", "MSFT", "NVDA", "TSLA", "AMD", "META", "AMZN", "SPY", "QQQ", "PLTR", "COIN",
    }]
    hits = []
    for sym in focus:
        msg = _option_unusual(sym)
        if msg:
            hits.append(f"• {msg}")
    if not hits:
        lines.append("لا ظهور واضح الآن (أو البيانات محدودة مجانًا).")
    else:
        lines.extend(hits[:10])
    lines.append("")
    lines.append("خيارات = رافعة عالية — للمراقبة فقط في وضع التنبيهات.")
    return "\n".join(lines)


def format_sector_etfs() -> str:
    """57 Sector ETF monitor."""
    lines = ["🧭 مراقبة ETF القطاعية", ""]
    rows = []
    for etf, name in SECTOR_ETFS.items():
        try:
            df = fetch_history(etf, period="1mo", interval="1d")
            if df.empty or len(df) < 3:
                continue
            last = float(df["Close"].iloc[-1])
            prev = float(df["Close"].iloc[-2])
            chg = ((last / prev) - 1) * 100
            rows.append((chg, etf, name, last))
        except Exception:
            continue
    rows.sort(reverse=True)
    if not rows:
        lines.append("تعذّر جلب بيانات القطاعات.")
        return "\n".join(lines)
    lines.append("الأقوى اليوم:")
    for chg, etf, name, last in rows[:4]:
        lines.append(f"  🟢 {etf} ({name}) {chg:+.2f}%")
    lines.append("الأضعف اليوم:")
    for chg, etf, name, last in rows[-4:]:
        lines.append(f"  🔴 {etf} ({name}) {chg:+.2f}%")
    lines.append("")
    lines.append("تداول مع اتجاه القطاع غالبًا أوضح من عكس التيار.")
    return "\n".join(lines)

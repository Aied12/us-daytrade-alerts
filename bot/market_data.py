from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf


SECTOR_MAP = {
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology", "AMD": "Technology",
    "AVGO": "Technology", "CRM": "Technology", "PLTR": "Technology", "META": "Technology",
    "GOOGL": "Technology", "NFLX": "Communication", "DIS": "Communication",
    "AMZN": "Consumer", "TSLA": "Consumer", "COST": "Consumer", "UBER": "Consumer",
    "JPM": "Financials", "BAC": "Financials", "COIN": "Financials",
    "XOM": "Energy", "CVX": "Energy",
    "BA": "Industrial",
    "SPY": "Index", "QQQ": "Index", "IWM": "Index",
}

NEGATIVE_NEWS_WORDS = (
    "fraud", "lawsuit", "probe", "investigation", "downgrade", "bankruptcy",
    "sec ", "recall", "layoff", "misses", "plunge", "crash", "default",
    "تهمة", "تحقيق", "دعوى", "إفلاس", "تخفيض",
)


@dataclass
class QuoteSnapshot:
    symbol: str
    last: float
    open: float
    high: float
    low: float
    prev_close: float
    volume: float
    avg_volume_20: float
    change_pct: float
    gap_pct: float
    range_pct: float
    rsi_14: float
    above_vwap_proxy: bool
    day_high: float
    day_low: float
    # extended
    ma20: float = 0.0
    ma50: float = 0.0
    ma20_prev: float = 0.0
    ma50_prev: float = 0.0
    high_20: float = 0.0
    low_20: float = 0.0
    atr_pct: float = 0.0
    vwap_proxy: float = 0.0
    corr_spy: float = 0.0
    corr_qqq: float = 0.0
    sector: str = "Unknown"
    days_to_earnings: int | None = None
    candle_pattern: str = ""
    support: float = 0.0
    resistance: float = 0.0
    news_negative: bool = False
    open_range_proxy_pct: float = 0.0  # (high-low)/prev for day as OR proxy


def _rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return float(val) if pd.notna(val) else 50.0


def fetch_history(symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    from bot.cacheutil import API_THRIFT, LIGHT_MODE, cached_call

    # 85/86/95 — shorter history + disk cache in thrift/light modes
    if LIGHT_MODE and period in ("6mo", "3mo"):
        period = "3mo" if interval == "1d" else period

    cache_key = f"hist:{symbol}:{period}:{interval}"

    def _download():
        df = yf.download(
            symbol,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=True,
            threads=False,
        )
        if df is None or df.empty:
            return []
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        df = df.dropna()
        # serialize for cache
        out = []
        for idx, row in df.iterrows():
            out.append(
                {
                    "Date": str(idx),
                    "Open": float(row["Open"]),
                    "High": float(row["High"]),
                    "Low": float(row["Low"]),
                    "Close": float(row["Close"]),
                    "Volume": float(row["Volume"]),
                }
            )
        return out

    try:
        raw = cached_call(cache_key, _download) if (API_THRIFT or LIGHT_MODE) else _download()
    except Exception:
        raw = _download()

    if not raw:
        return pd.DataFrame()
    df = pd.DataFrame(raw)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date")
    return df


def _atr_pct(df: pd.DataFrame, period: int = 14) -> float:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = float(tr.tail(period).mean())
    last = float(close.iloc[-1])
    return (atr / last) * 100 if last else 0.0


def _corr(a: pd.Series, b: pd.Series, window: int = 20) -> float:
    aligned = pd.concat([a.pct_change(), b.pct_change()], axis=1).dropna().tail(window)
    if len(aligned) < 10:
        return 0.0
    val = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
    return float(val) if pd.notna(val) else 0.0


def _candle_pattern(o: float, h: float, l: float, c: float, prev_o: float, prev_c: float) -> str:
    body = abs(c - o)
    full = max(h - l, 1e-9)
    upper = h - max(c, o)
    lower = min(c, o) - l
    if body / full < 0.1:
        return "دوجي"
    if lower > body * 2 and upper < body * 0.5 and c >= o:
        return "مطرقة"
    if upper > body * 2 and lower < body * 0.5 and c <= o:
        return "شهاب"
    # engulfing
    if c > o and prev_c < prev_o and c >= prev_o and o <= prev_c:
        return "ابتلاع صاعد"
    if c < o and prev_c > prev_o and c <= prev_o and o >= prev_c:
        return "ابتلاع هابط"
    return ""


def _support_resistance(df: pd.DataFrame) -> tuple[float, float]:
    window = df.tail(20)
    # simple swing: min low / max high excluding today extremes slightly
    support = float(window["Low"].nsmallest(3).mean())
    resistance = float(window["High"].nlargest(3).mean())
    return support, resistance


def _cache_get(path: Path, max_age_sec: int = 43200) -> object | None:
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > max_age_sec:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cache_set(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _days_to_earnings(symbol: str) -> int | None:
    cache = Path(__file__).resolve().parent.parent / "data" / "cache" / f"earn_{symbol}.json"
    cached = _cache_get(cache)
    if cached is not None:
        return cached.get("days")
    days = None
    try:
        t = yf.Ticker(symbol)
        ed = None
        try:
            ed = t.get_earnings_dates(limit=4)
        except Exception:
            ed = None
        if ed is not None and not getattr(ed, "empty", True):
            idx = pd.to_datetime(ed.index)
            today = pd.Timestamp.utcnow().tz_localize(None).normalize()
            future = []
            for d in idx:
                dd = pd.Timestamp(d).tz_localize(None).normalize()
                if dd >= today:
                    future.append(dd)
            if future:
                days = int((min(future) - today).days)
    except Exception:
        days = None
    _cache_set(cache, {"days": days})
    return days


def _news_negative(symbol: str) -> bool:
    cache = Path(__file__).resolve().parent.parent / "data" / "cache" / f"news_{symbol}.json"
    cached = _cache_get(cache, max_age_sec=7200)
    if cached is not None:
        return bool(cached.get("neg"))
    neg = False
    try:
        news = yf.Ticker(symbol).news or []
        for item in news[:5]:
            title = (item.get("title") or "").lower()
            if any(w in title for w in NEGATIVE_NEWS_WORDS):
                neg = True
                break
    except Exception:
        neg = False
    _cache_set(cache, {"neg": neg})
    return neg


_bench_cache: dict[str, pd.DataFrame] = {}


def _bench(symbol: str) -> pd.DataFrame:
    if symbol not in _bench_cache:
        _bench_cache[symbol] = fetch_history(symbol, period="6mo", interval="1d")
    return _bench_cache[symbol]


def build_snapshot(symbol: str) -> Optional[QuoteSnapshot]:
    daily = fetch_history(symbol, period="6mo", interval="1d")
    if daily.empty or len(daily) < 55:
        return None

    last_row = daily.iloc[-1]
    prev = daily.iloc[-2]
    last = float(last_row["Close"])
    open_px = float(last_row["Open"])
    high = float(last_row["High"])
    low = float(last_row["Low"])
    prev_close = float(prev["Close"])
    volume = float(last_row["Volume"])
    avg_vol = float(daily["Volume"].tail(20).mean())
    change_pct = ((last - prev_close) / prev_close) * 100 if prev_close else 0.0
    gap_pct = ((open_px - prev_close) / prev_close) * 100 if prev_close else 0.0
    range_pct = ((high - low) / prev_close) * 100 if prev_close else 0.0
    rsi = _rsi(daily["Close"])

    ma20 = float(daily["Close"].rolling(20).mean().iloc[-1])
    ma50 = float(daily["Close"].rolling(50).mean().iloc[-1])
    ma20_prev = float(daily["Close"].rolling(20).mean().iloc[-2])
    ma50_prev = float(daily["Close"].rolling(50).mean().iloc[-2])
    high_20 = float(daily["High"].tail(20).max())
    low_20 = float(daily["Low"].tail(20).min())
    atr_pct = _atr_pct(daily)

    typical = (daily["High"] + daily["Low"] + daily["Close"]) / 3
    vwap_proxy = float((typical * daily["Volume"]).tail(5).sum() / max(daily["Volume"].tail(5).sum(), 1))

    spy = _bench("SPY")
    qqq = _bench("QQQ")
    corr_spy = _corr(daily["Close"], spy["Close"]) if not spy.empty else 0.0
    corr_qqq = _corr(daily["Close"], qqq["Close"]) if not qqq.empty else 0.0

    support, resistance = _support_resistance(daily)
    pattern = _candle_pattern(
        open_px, high, low, last,
        float(prev["Open"]), float(prev["Close"]),
    )

    return QuoteSnapshot(
        symbol=symbol.upper(),
        last=last,
        open=open_px,
        high=high,
        low=low,
        prev_close=prev_close,
        volume=volume,
        avg_volume_20=avg_vol,
        change_pct=change_pct,
        gap_pct=gap_pct,
        range_pct=range_pct,
        rsi_14=rsi,
        above_vwap_proxy=last >= vwap_proxy,
        day_high=high,
        day_low=low,
        ma20=ma20,
        ma50=ma50,
        ma20_prev=ma20_prev,
        ma50_prev=ma50_prev,
        high_20=high_20,
        low_20=low_20,
        atr_pct=atr_pct,
        vwap_proxy=vwap_proxy,
        corr_spy=corr_spy,
        corr_qqq=corr_qqq,
        sector=SECTOR_MAP.get(symbol.upper(), "Unknown"),
        days_to_earnings=_days_to_earnings(symbol),
        candle_pattern=pattern,
        support=support,
        resistance=resistance,
        news_negative=_news_negative(symbol),
        open_range_proxy_pct=range_pct,
    )


def market_context() -> dict:
    from bot.live_quotes import apply_live_quote, fetch_live_quotes, price_lag_label_ar, quote_provider

    live = fetch_live_quotes(("SPY", "QQQ", "IWM"))
    out = {}
    for sym in ("SPY", "QQQ", "IWM"):
        snap = build_snapshot(sym)
        snap = apply_live_quote(snap, live.get(sym))
        if snap:
            out[sym] = {
                "last": snap.last,
                "change_pct": round(snap.change_pct, 2),
                "rsi": round(snap.rsi_14, 1),
            }
    if not out:
        return {
            "tone": "غير متاح",
            "details": {},
            "no_trade_today": False,
            "avg_change_pct": 0.0,
            "price_provider": quote_provider(),
            "price_lag_ar": price_lag_label_ar(),
        }

    avg_chg = sum(v["change_pct"] for v in out.values()) / len(out)
    if avg_chg >= 0.6:
        tone = "صاعد — مناسب لمطاردة الزخم بحذر"
    elif avg_chg <= -0.6:
        tone = "هابط — فضّل الانتظار أو صفقات قصيرة فقط للمحترفين"
    else:
        tone = "متذبذب — ركّز على أفضل 2–3 فرص فقط"

    # Don't trade today heuristic
    spy = out.get("SPY", {})
    qqq = out.get("QQQ", {})
    no_trade = False
    reasons = []
    if abs(avg_chg) >= 2.0:
        no_trade = True
        reasons.append("تحرّك عنيف في المؤشرات")
    if spy.get("rsi", 50) > 82 or spy.get("rsi", 50) < 18:
        no_trade = True
        reasons.append("RSI مؤشر متطرف")

    return {
        "tone": tone,
        "avg_change_pct": round(avg_chg, 2),
        "details": out,
        "no_trade_today": no_trade,
        "no_trade_reasons": reasons,
        "price_provider": quote_provider(),
        "price_lag_ar": price_lag_label_ar(),
    }


def sector_momentum(snapshots: list[QuoteSnapshot]) -> dict[str, float]:
    buckets: dict[str, list[float]] = {}
    for s in snapshots:
        if s.sector in ("Index", "Unknown"):
            continue
        buckets.setdefault(s.sector, []).append(s.change_pct)
    return {k: round(sum(v) / len(v), 2) for k, v in buckets.items() if v}


def scan_watchlist(symbols: list[str]) -> list[QuoteSnapshot]:
    from bot.cacheutil import light_watchlist
    from bot.live_quotes import apply_live_quote, fetch_live_quotes

    symbols = light_watchlist(symbols)
    live = fetch_live_quotes(symbols)
    snaps: list[QuoteSnapshot] = []
    for sym in symbols:
        try:
            snap = build_snapshot(sym)
            snap = apply_live_quote(snap, live.get(sym.upper()))
            if snap:
                snaps.append(snap)
        except Exception:
            continue
    return snaps

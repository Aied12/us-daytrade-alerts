from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd
import yfinance as yf


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


def _rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return float(val) if pd.notna(val) else 50.0


def fetch_history(symbol: str, period: str = "3mo", interval: str = "1d") -> pd.DataFrame:
    df = yf.download(
        symbol,
        period=period,
        interval=interval,
        progress=False,
        auto_adjust=True,
        threads=False,
    )
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    return df.dropna()


def build_snapshot(symbol: str) -> Optional[QuoteSnapshot]:
    daily = fetch_history(symbol, period="3mo", interval="1d")
    if daily.empty or len(daily) < 25:
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

    # Intraday VWAP proxy from daily OHLC (approx): typical price weighted
    typical = (daily["High"] + daily["Low"] + daily["Close"]) / 3
    vwap_proxy = float((typical * daily["Volume"]).tail(5).sum() / daily["Volume"].tail(5).sum())

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
    )


def market_context() -> dict:
    """Broad market tone using SPY / QQQ / IWM."""
    out = {}
    for sym in ("SPY", "QQQ", "IWM"):
        snap = build_snapshot(sym)
        if snap:
            out[sym] = {
                "last": snap.last,
                "change_pct": round(snap.change_pct, 2),
                "rsi": round(snap.rsi_14, 1),
            }
    if not out:
        return {"tone": "غير متاح", "details": {}}

    avg_chg = sum(v["change_pct"] for v in out.values()) / len(out)
    if avg_chg >= 0.6:
        tone = "صاعد — مناسب لمطاردة الزخم بحذر"
    elif avg_chg <= -0.6:
        tone = "هابط — فضّل الانتظار أو صفقات قصيرة فقط للمحترفين"
    else:
        tone = "متذبذب — ركّز على أفضل 2–3 فرص فقط"
    return {"tone": tone, "avg_change_pct": round(avg_chg, 2), "details": out}


def scan_watchlist(symbols: list[str]) -> list[QuoteSnapshot]:
    snaps: list[QuoteSnapshot] = []
    for sym in symbols:
        try:
            snap = build_snapshot(sym)
            if snap:
                snaps.append(snap)
        except Exception:
            continue
    return snaps

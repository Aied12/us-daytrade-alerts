from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from bot.market_data import fetch_history


def make_daily_chart(symbol: str, out_path: Path, days: int = 60) -> Path | None:
    df = fetch_history(symbol, period="3mo", interval="1d")
    if df is None or df.empty or len(df) < 10:
        return None

    df = df.tail(days).copy()
    df["MA20"] = df["Close"].rolling(20).mean()

    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=140)
    ax.plot(df.index, df["Close"], color="#0f766e", linewidth=2.0, label=symbol)
    if df["MA20"].notna().any():
        ax.plot(df.index, df["MA20"], color="#b45309", linewidth=1.3, label="MA20")
    ax.set_title(f"{symbol} — آخر {len(df)} يوم", fontsize=12, pad=10)
    ax.set_ylabel("USD")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="upper left", frameon=False)
    fig.autofmt_xdate()
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor="white")
    plt.close(fig)
    return out_path


def make_market_poster(out_path: Path, symbols: list[str] | None = None) -> Path | None:
    symbols = symbols or ["SPY", "QQQ", "IWM"]
    fig, axes = plt.subplots(len(symbols), 1, figsize=(8, 2.6 * len(symbols)), dpi=130)
    if len(symbols) == 1:
        axes = [axes]
    ok = False
    for ax, sym in zip(axes, symbols):
        df = fetch_history(sym, period="3mo", interval="1d")
        if df is None or df.empty:
            ax.set_title(f"{sym} — لا بيانات")
            continue
        df = df.tail(45)
        ax.plot(df.index, df["Close"], color="#115e59", linewidth=1.8)
        chg = (df["Close"].iloc[-1] / df["Close"].iloc[-2] - 1) * 100
        ax.set_title(f"{sym}  {chg:+.2f}%", loc="left")
        ax.grid(True, alpha=0.2)
        ok = True
    if not ok:
        plt.close(fig)
        return None
    fig.suptitle("ملصق السوق اليومي", fontsize=13)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor="#f8fafc")
    plt.close(fig)
    return out_path

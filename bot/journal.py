from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from bot.config import Settings


JOURNAL_FIELDS = [
    "timestamp_utc",
    "symbol",
    "side",
    "shares",
    "entry",
    "exit",
    "pnl_sar",
    "notes",
]


def journal_path(settings: Settings) -> Path:
    return settings.data_dir / "journal.csv"


def ensure_journal(settings: Settings) -> Path:
    path = journal_path(settings)
    if not path.exists():
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=JOURNAL_FIELDS)
            writer.writeheader()
    return path


def add_trade(
    settings: Settings,
    symbol: str,
    side: str,
    shares: int,
    entry: float,
    exit_px: float,
    notes: str = "",
) -> dict:
    path = ensure_journal(settings)
    pnl_usd = (exit_px - entry) * shares if side == "long" else (entry - exit_px) * shares
    pnl_sar = pnl_usd * settings.usd_sar_rate
    row = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol.upper(),
        "side": side,
        "shares": shares,
        "entry": entry,
        "exit": exit_px,
        "pnl_sar": round(pnl_sar, 2),
        "notes": notes,
    }
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=JOURNAL_FIELDS)
        writer.writerow(row)
    return row


def summarize_journal(settings: Settings) -> str:
    path = ensure_journal(settings)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    if not rows:
        return "دفتر الصفقات فارغ — ابدأ بتسجيل أول صفقة بعد التنفيذ اليدوي."
    total = sum(float(r["pnl_sar"]) for r in rows)
    wins = sum(1 for r in rows if float(r["pnl_sar"]) > 0)
    losses = sum(1 for r in rows if float(r["pnl_sar"]) < 0)
    return (
        f"عدد الصفقات: {len(rows)} | رابحة: {wins} | خاسرة: {losses}\n"
        f"صافي الربح/الخسارة: {total:,.2f} ر.س\n"
        f"الملف: {path}"
    )

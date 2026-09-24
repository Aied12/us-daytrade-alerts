#!/usr/bin/env python3
"""Write docs/prices-live.json with current extended-hours prices for the dashboard."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.config import load_settings
from bot.live_quotes import fetch_live_quotes, session_phase


def main() -> int:
    settings = load_settings()
    symbols = list(dict.fromkeys(settings.watchlist + ["SPY", "QQQ", "IWM"]))
    try:
        status = json.loads((ROOT / "docs" / "status.json").read_text(encoding="utf-8"))
        for g in (status.get("gainers") or [])[:15]:
            if g.get("symbol"):
                symbols.append(g["symbol"])
        for o in (status.get("opportunities") or [])[:15]:
            if o.get("symbol"):
                symbols.append(o["symbol"])
        for m in (status.get("momentum_scanner") or [])[:10]:
            if m.get("symbol"):
                symbols.append(m["symbol"])
        for s in (status.get("sniper_scanner") or [])[:12]:
            if s.get("symbol"):
                symbols.append(s["symbol"])
    except Exception:
        pass
    symbols = list(dict.fromkeys(s.upper() for s in symbols if s))[:55]
    quotes = fetch_live_quotes(symbols)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_ts": int(datetime.now(timezone.utc).timestamp()),
        "phase": session_phase(),
        "quotes": {
            sym: {
                "last": round(q.last, 4),
                "change_pct": round(q.change_pct, 4),
                "volume": round(float(getattr(q, "volume", 0) or 0), 0),
                "source": q.source,
            }
            for sym, q in quotes.items()
        },
    }
    raw = json.dumps(payload, ensure_ascii=False)
    for d in (ROOT / "docs", ROOT / "pages"):
        d.mkdir(parents=True, exist_ok=True)
        for name in ("prices-live.json", "live.json"):
            (d / name).write_text(raw, encoding="utf-8")
            print("wrote", d / name, "n=", len(payload["quotes"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

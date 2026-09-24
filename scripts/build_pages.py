#!/usr/bin/env python3
"""Build static status.json for GitHub Pages (permanent free dashboard)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.config import load_settings
from bot.holidays import holiday_note, is_trading_day
from bot.ops import read_status


def main() -> None:
    settings = load_settings()
    out_dir = ROOT / "pages"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": read_status(),
        "telegram_enabled": settings.telegram_enabled,
        "mode": settings.user_mode,
        "light_mode": settings.light_mode,
        "timezone": settings.timezone_name,
        "trading_day": is_trading_day(),
        "holiday_note": holiday_note(),
        "users": len(settings.all_private_chat_ids),
        "bot": "@Aied01_bot",
        "channel": "@aied01",
    }
    (out_dir / "status.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("wrote", out_dir / "status.json")


if __name__ == "__main__":
    main()

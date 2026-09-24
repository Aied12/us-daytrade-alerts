from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from bot.config import ROOT, Settings, load_settings


def backup_settings(settings: Settings | None = None) -> Path:
    """83 — backup .env (redacted copy + full private backup)."""
    settings = settings or load_settings()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    bak_dir = settings.data_dir / "backups"
    bak_dir.mkdir(parents=True, exist_ok=True)

    env_path = ROOT / ".env"
    full = bak_dir / f"env_full_{stamp}.bak"
    redacted = bak_dir / f"env_redacted_{stamp}.txt"
    if env_path.exists():
        shutil.copy2(env_path, full)
        text = env_path.read_text(encoding="utf-8")
        safe = re.sub(
            r"(TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID|TELEGRAM_CHANNEL_ID)=.*",
            r"\1=***REDACTED***",
            text,
        )
        redacted.write_text(safe, encoding="utf-8")

    meta = {
        "created_at": stamp,
        "capital_sar": settings.capital_sar,
        "user_mode": settings.user_mode,
        "min_price_usd": settings.min_price_usd,
        "watchlist": settings.watchlist,
        "extra_chat_ids": settings.extra_chat_ids,
    }
    (bak_dir / f"meta_{stamp}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # keep last 20 full backups
    fulls = sorted(bak_dir.glob("env_full_*.bak"), reverse=True)
    for old in fulls[20:]:
        old.unlink(missing_ok=True)
    return full


def mask_token(token: str) -> str:
    """90 — never print full token."""
    if not token:
        return "(empty)"
    if len(token) < 12:
        return "***"
    return token[:6] + "…" + token[-4:]

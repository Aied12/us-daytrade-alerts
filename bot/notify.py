from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import requests

from bot.config import Settings


def send_telegram(settings: Settings, text: str) -> bool:
    if not settings.telegram_enabled:
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    # Telegram limit ~4096 chars
    chunks = [text[i : i + 3500] for i in range(0, len(text), 3500)] or [text]
    ok = True
    for chunk in chunks:
        resp = requests.post(
            url,
            json={
                "chat_id": settings.telegram_chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            ok = False
    return ok


def deliver(settings: Settings, title: str, text: str, also_print: bool = True) -> None:
    if also_print:
        print("\n" + "=" * 60)
        print(title)
        print("=" * 60)
        print(text)

    log_path = settings.logs_dir / "alerts.log"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"\n[{datetime.now(timezone.utc).isoformat()}] {title}\n{text}\n")

    if settings.telegram_enabled:
        sent = send_telegram(settings, f"{title}\n\n{text}")
        print(f"[telegram] {'OK' if sent else 'FAILED'}")
    else:
        print("[telegram] غير مفعّل — ضع TELEGRAM_BOT_TOKEN و TELEGRAM_CHAT_ID في .env")


def save_json_snapshot(settings: Settings, payload: dict, name: str) -> Path:
    path = settings.data_dir / name
    serializable = {
        k: v
        for k, v in payload.items()
        if k in ("morning", "evening", "sent_symbols", "intraday")
    }
    # also store compact signal summary
    signals = payload.get("signals") or []
    serializable["signal_summary"] = [
        {
            "symbol": s.symbol,
            "action": s.action.value,
            "score": s.score,
            "reason": s.reason,
            "entry": s.entry_hint,
            "stop": s.stop_hint,
            "target": s.target_hint,
        }
        for s in signals[:20]
    ]
    path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")
    return path

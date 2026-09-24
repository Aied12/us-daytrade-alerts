from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from bot.config import Settings
from bot.formatters import public_channel_text


def _api(settings: Settings, method: str) -> str:
    return f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"


def _post(settings: Settings, method: str, **kwargs) -> dict:
    resp = requests.post(_api(settings, method), timeout=60, **kwargs)
    try:
        data = resp.json()
    except Exception:
        data = {"ok": False, "status": resp.status_code, "text": resp.text[:200]}
    return data


def send_telegram(
    settings: Settings,
    text: str,
    *,
    chat_id: str | None = None,
    reply_markup: dict | None = None,
    also_channel: bool = False,
) -> bool:
    if not settings.telegram_bot_token:
        return False
    targets = [chat_id or settings.telegram_chat_id]
    if also_channel and settings.telegram_channel_id:
        targets.append(settings.telegram_channel_id)

    ok_any = False
    for target in targets:
        if not target:
            continue
        body = text
        if target == settings.telegram_channel_id:
            body = public_channel_text(text)
        for i in range(0, max(len(body), 1), 3500):
            chunk = body[i : i + 3500]
            payload: dict[str, Any] = {
                "chat_id": target,
                "text": chunk,
                "disable_web_page_preview": True,
            }
            # buttons only on private first chunk
            if reply_markup and target == settings.telegram_chat_id and i == 0:
                payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
            data = _post(settings, "sendMessage", json=payload)
            ok_any = ok_any or bool(data.get("ok"))
    return ok_any


def send_photo(
    settings: Settings,
    photo_path: Path,
    caption: str = "",
    *,
    also_channel: bool = False,
) -> bool:
    if not settings.telegram_enabled or not photo_path.exists():
        return False
    targets = [settings.telegram_chat_id]
    if also_channel and settings.telegram_channel_id:
        targets.append(settings.telegram_channel_id)
    ok = False
    for target in targets:
        with photo_path.open("rb") as f:
            data = _post(
                settings,
                "sendPhoto",
                data={"chat_id": target, "caption": caption[:1000]},
                files={"photo": f},
            )
        ok = ok or bool(data.get("ok"))
    return ok


def send_voice(settings: Settings, voice_path: Path, caption: str = "") -> bool:
    if not settings.telegram_enabled or not voice_path.exists():
        return False
    with voice_path.open("rb") as f:
        data = _post(
            settings,
            "sendVoice",
            data={"chat_id": settings.telegram_chat_id, "caption": caption[:900]},
            files={"voice": f},
        )
    return bool(data.get("ok"))


def answer_callback(settings: Settings, callback_id: str, text: str) -> None:
    _post(
        settings,
        "answerCallbackQuery",
        json={"callback_query_id": callback_id, "text": text, "show_alert": False},
    )


def set_bot_commands(settings: Settings) -> bool:
    commands = [
        {"command": "start", "description": "ابدأ"},
        {"command": "scan", "description": "فحص السوق الآن"},
        {"command": "risk", "description": "عرض المخاطرة ورأس المال"},
        {"command": "journal", "description": "ملخص دفتر الصفقات"},
        {"command": "mode", "description": "مبتدئ أو محترف"},
        {"command": "chart", "description": "ملصق السوق اليومي"},
        {"command": "voice", "description": "ملخص صوتي قصير"},
        {"command": "strategies", "description": "مقارنة أداء الاستراتيجيات"},
        {"command": "help", "description": "المساعدة"},
    ]
    data = _post(settings, "setMyCommands", json={"commands": commands})
    return bool(data.get("ok"))


def deliver(
    settings: Settings,
    title: str,
    text: str,
    *,
    also_print: bool = True,
    reply_markup: dict | None = None,
    also_channel: bool = False,
) -> None:
    full = f"{title}\n\n{text}" if title else text
    if also_print:
        print("\n" + "=" * 60)
        print(title)
        print("=" * 60)
        print(text)

    log_path = settings.logs_dir / "alerts.log"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"\n[{datetime.now(timezone.utc).isoformat()}] {title}\n{text}\n")

    if settings.telegram_enabled:
        sent = send_telegram(
            settings,
            full,
            reply_markup=reply_markup,
            also_channel=also_channel,
        )
        print(f"[telegram] {'OK' if sent else 'FAILED'}")
    else:
        print("[telegram] غير مفعّل")


def save_json_snapshot(settings: Settings, payload: dict, name: str) -> Path:
    path = settings.data_dir / name
    serializable = {
        k: v
        for k, v in payload.items()
        if k in ("morning", "evening", "sent_symbols", "intraday")
    }
    signals = payload.get("signals") or []
    serializable["signal_summary"] = [
        {
            "symbol": s.symbol,
            "action": s.action.value,
            "score": s.score,
            "score_100": getattr(s, "score_100", None),
            "strategies": getattr(s, "strategies", [])[:5],
            "reason": s.reason,
            "entry": s.entry_hint,
            "stop": s.stop_hint,
            "target": s.target_hint,
        }
        for s in signals[:20]
    ]
    path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")
    return path

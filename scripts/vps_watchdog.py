#!/usr/bin/env python3
"""VPS watchdog: detect stalled scan loop, restart containers, alert."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STATUS = ROOT / "docs" / "status.json"
GATE = ROOT / "data" / "watchdog_gate.json"
LOG = ROOT / "logs" / "vps_watchdog.log"
COMPOSE = ROOT / "deploy" / "vps" / "docker-compose.yml"

# Stale after this many seconds without fresh status.json
STALE_SEC = int(os.getenv("WATCHDOG_STALE_SEC", "180"))
# Don't spam alerts more often than this
ALERT_COOLDOWN = int(os.getenv("WATCHDOG_ALERT_COOLDOWN", "1800"))


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_gate() -> dict:
    try:
        if GATE.exists():
            return json.loads(GATE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_gate(g: dict) -> None:
    GATE.parent.mkdir(parents=True, exist_ok=True)
    GATE.write_text(json.dumps(g, ensure_ascii=False, indent=2), encoding="utf-8")


def status_age() -> int | None:
    if not STATUS.exists():
        return None
    return int(time.time() - STATUS.stat().st_mtime)


def docker_ps() -> str:
    try:
        return subprocess.check_output(
            ["docker", "compose", "-f", str(COMPOSE), "ps", "--format", "json"],
            cwd=str(COMPOSE.parent),
            text=True,
            timeout=30,
        )
    except Exception as e:
        return f"error:{e}"


def containers_ok() -> tuple[bool, str]:
    raw = docker_ps()
    if raw.startswith("error:"):
        return False, raw
    names_needed = {"bot", "web", "caddy", "api"}
    up: set[str] = set()
    # compose json may be NDJSON or array depending on version
    chunks = []
    raw_s = raw.strip()
    if raw_s.startswith("["):
        try:
            chunks = json.loads(raw_s)
        except Exception:
            chunks = []
    else:
        for line in raw_s.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                chunks.append(json.loads(line))
            except Exception:
                pass
    for row in chunks:
        svc = str(row.get("Service") or row.get("Name") or "").lower()
        state = str(row.get("State") or row.get("Status") or "").lower()
        for need in list(names_needed):
            if need in svc and ("running" in state or "up" in state):
                up.add(need)
    missing = sorted(names_needed - up)
    if missing:
        return False, f"down={','.join(missing)} up={','.join(sorted(up))}"
    return True, "all_up"


def restart_stack() -> bool:
    try:
        subprocess.check_call(
            ["docker", "compose", "-f", str(COMPOSE), "up", "-d", "--no-build"],
            cwd=str(COMPOSE.parent),
            timeout=120,
        )
        return True
    except Exception as e:
        log(f"restart fail: {e}")
        return False


def alert(text: str) -> None:
    gate = load_gate()
    last = float(gate.get("last_alert_ts") or 0)
    now = time.time()
    if now - last < ALERT_COOLDOWN:
        log(f"alert suppressed cooldown: {text}")
        return
    gate["last_alert_ts"] = now
    gate["last_alert"] = text
    save_gate(gate)

    # Web push
    try:
        from bot.web_push import send_web_push

        n = send_web_push("⚠ توقف الفحص", text, url="/")
        log(f"webpush devices={n}")
    except Exception as e:
        log(f"webpush fail: {e}")

    # Telegram ops alert (works even if TELEGRAM_ENABLED=0)
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if token and chat:
        try:
            import requests

            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": f"⚠ VPS Watchdog\n{text}", "disable_web_page_preview": True},
                timeout=30,
            )
            log(f"telegram status={r.status_code} ok={r.json().get('ok')}")
        except Exception as e:
            log(f"telegram fail: {e}")
    else:
        log("telegram skipped (no token/chat)")


def load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    load_dotenv()
    age = status_age()
    ok_c, cmsg = containers_ok()
    log(f"check age={age} containers={cmsg}")

    problems: list[str] = []
    if age is None:
        problems.append("status.json مفقود")
    elif age > STALE_SEC:
        problems.append(f"آخر فحص متوقف منذ {age}ث (حد {STALE_SEC}ث)")
    if not ok_c:
        problems.append(f"حاويات: {cmsg}")

    if not problems:
        gate = load_gate()
        gate["last_ok_ts"] = time.time()
        gate["last_age"] = age
        save_gate(gate)
        return 0

    # Try recover first
    log("problems: " + " | ".join(problems))
    restarted = restart_stack()
    time.sleep(8)
    age2 = status_age()
    ok2, cmsg2 = containers_ok()
    recovered = ok2 and age2 is not None and age2 <= STALE_SEC + 30
    detail = " | ".join(problems) + f" · restart={'ok' if restarted else 'fail'} · now_age={age2} · {cmsg2}"
    if recovered:
        log("recovered after restart: " + detail)
        return 0

    alert(detail)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

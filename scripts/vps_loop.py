#!/usr/bin/env python3
"""Continuous publisher loop for VPS — no GitHub Pages dependency for freshness.

Priority: refresh status/live first so «آخر فحص» stays fresh (~45–60s).
Slow jobs (news ~3min, scheduled tick) run in the background and never
block the next status cycle.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CYCLE_SEC = int(os.getenv("VPS_CYCLE_SEC", "45"))
# 0 = لا تدفع Git (موصى به على VPS — الصفحة تُخدم محلياً)
PUSH_EVERY = int(os.getenv("VPS_PUSH_EVERY", "0"))
# جلب الأخبار بطيء (~3 دقائق) — خلفية فقط
NEWS_EVERY_SEC = int(os.getenv("VPS_NEWS_EVERY_SEC", "300"))
LIVE_API_URL = (os.getenv("LIVE_API_URL") or "").rstrip("/")
LIVE_API_TOKEN = os.getenv("LIVE_API_TOKEN") or ""
_last_news_ts = 0.0
_bg: dict[str, subprocess.Popen | None] = {"news": None, "tick": None}


def run(cmd: list[str]) -> int:
    print("+", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=ROOT)


def mirror(path: Path, endpoint: str) -> None:
    if not LIVE_API_URL or not LIVE_API_TOKEN or not path.exists():
        return
    req = urllib.request.Request(
        f"{LIVE_API_URL}{endpoint}",
        data=path.read_bytes(),
        headers={
            "Authorization": f"Bearer {LIVE_API_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"[mirror] {endpoint} -> {r.status}", flush=True)
    except Exception as e:
        print(f"[mirror] fail {endpoint}: {e}", flush=True)


def _reap(name: str) -> bool:
    """Return True if still running. Reap finished processes."""
    proc = _bg.get(name)
    if proc is None:
        return False
    rc = proc.poll()
    if rc is None:
        return True
    if rc != 0:
        print(f"[vps_loop] {name}_bg exit={rc}", flush=True)
    else:
        print(f"[vps_loop] {name}_bg done", flush=True)
        if name == "news":
            mirror(ROOT / "docs" / "news-live.json", "/ingest/news")
    _bg[name] = None
    return False


def start_bg(name: str, cmd: list[str]) -> bool:
    if _reap(name):
        print(f"[vps_loop] {name}_bg still running — skip", flush=True)
        return False
    print("+", " ".join(cmd), "(bg)", flush=True)
    _bg[name] = subprocess.Popen(
        cmd,
        cwd=ROOT,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    return True


def cycle(n: int) -> None:
    global _last_news_ts
    py = sys.executable
    t0 = time.time()

    # 1) حدّث الأسعار + status أولاً — هذا ما يراه «آخر فحص»
    run([py, str(ROOT / "scripts" / "write_live_json.py")])
    run([py, str(ROOT / "scripts" / "build_pages.py")])
    src = ROOT / "pages" / "index.html"
    dst = ROOT / "docs" / "index.html"
    if src.exists():
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    mirror(ROOT / "docs" / "status.json", "/ingest")
    mirror(ROOT / "docs" / "prices-live.json", "/ingest/prices")
    print(f"[vps_loop] status_ready={time.time() - t0:.1f}s", flush=True)

    # 2) أعمال بطيئة في الخلفية — لا تحجز الدورة التالية
    start_bg(
        "tick",
        [py, str(ROOT / "scripts" / "scheduled_run.py"), "--mode", "tick", "--force"],
    )

    _reap("news")
    due_news = (n == 1) or (time.time() - _last_news_ts >= NEWS_EVERY_SEC)
    if due_news and start_bg("news", [py, str(ROOT / "scripts" / "publish_news.py")]):
        _last_news_ts = time.time()

    if PUSH_EVERY > 0 and n % PUSH_EVERY == 0:
        run([py, str(ROOT / "scripts" / "publish_pages.py")])

    # Web Push سريع/fail-soft
    try:
        from bot.web_push import notify_urgent_from_status

        notify_urgent_from_status(ROOT / "docs" / "status.json")
    except Exception as e:
        print(f"[push] skip: {e}", flush=True)

    print(f"[vps_loop] cycle_work={time.time() - t0:.1f}s", flush=True)


def main() -> int:
    print(
        f"[vps_loop] start cycle={CYCLE_SEC}s push_every={PUSH_EVERY} "
        f"news_every={NEWS_EVERY_SEC}s bg=tick,news",
        flush=True,
    )
    n = 0
    while True:
        n += 1
        t0 = time.time()
        try:
            cycle(n)
        except Exception as e:
            print(f"[vps_loop] error: {e}", flush=True)
        slept = max(5, CYCLE_SEC - int(time.time() - t0))
        print(f"[vps_loop] cycle={n} sleep={slept}s", flush=True)
        time.sleep(slept)


if __name__ == "__main__":
    raise SystemExit(main())

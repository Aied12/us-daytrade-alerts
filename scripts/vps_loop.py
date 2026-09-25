#!/usr/bin/env python3
"""Continuous publisher loop for VPS — no GitHub Pages dependency for freshness.

Writes docs/*.json every cycle. Optionally mirrors to Cloudflare LIVE_API_URL.
Optionally pushes to git every PUSH_EVERY cycles (default 1 = each cycle).
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

CYCLE_SEC = int(os.getenv("VPS_CYCLE_SEC", "30"))
PUSH_EVERY = int(os.getenv("VPS_PUSH_EVERY", "2"))  # push git every N cycles
LIVE_API_URL = (os.getenv("LIVE_API_URL") or "").rstrip("/")
LIVE_API_TOKEN = os.getenv("LIVE_API_TOKEN") or ""


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


def cycle(n: int) -> None:
    py = sys.executable
    run([py, str(ROOT / "scripts" / "scheduled_run.py"), "--mode", "tick", "--force"])
    run([py, str(ROOT / "scripts" / "write_live_json.py")])
    run([py, str(ROOT / "scripts" / "publish_news.py")])
    run([py, str(ROOT / "scripts" / "build_pages.py")])
    # sync html
    src = ROOT / "pages" / "index.html"
    dst = ROOT / "docs" / "index.html"
    if src.exists():
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    mirror(ROOT / "docs" / "status.json", "/ingest")
    mirror(ROOT / "docs" / "prices-live.json", "/ingest/prices")
    mirror(ROOT / "docs" / "news-live.json", "/ingest/news")

    if PUSH_EVERY > 0 and n % PUSH_EVERY == 0:
        run([py, str(ROOT / "scripts" / "publish_pages.py")])


def main() -> int:
    print(f"[vps_loop] start cycle={CYCLE_SEC}s push_every={PUSH_EVERY}", flush=True)
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

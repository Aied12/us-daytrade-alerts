#!/usr/bin/env python3
"""Rebuild Pages JSON/HTML and push docs/ to GitHub for auto-refresh.

Uses an exclusive flock so overlapping cron ticks cannot pile up, stall git,
or cancel GitHub Pages builds.
"""

from __future__ import annotations

import fcntl
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK_PATH = ROOT / "data" / "publish_pages.lock"


def run(cmd: list[str]) -> int:
    print("+", " ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT)


def main() -> int:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_f = LOCK_PATH.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("[publish] skipped — another publish_pages is already running")
        return 0

    lock_f.seek(0)
    lock_f.truncate()
    lock_f.write(f"{int(time.time())}\n")
    lock_f.flush()

    py = ROOT / ".venv" / "bin" / "python"
    if not py.exists():
        py = Path(sys.executable)
    rc = run([str(py), str(ROOT / "scripts" / "build_pages.py")])
    if rc != 0:
        return rc
    run([str(py), str(ROOT / "scripts" / "write_live_json.py")])

    # Ensure docs HTML matches pages
    src = ROOT / "pages" / "index.html"
    dst = ROOT / "docs" / "index.html"
    if src.exists():
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    run(
        [
            "git",
            "add",
            "docs/index.html",
            "docs/status.json",
            "docs/prices-live.json",
            "docs/live.json",
            "pages/index.html",
            "pages/status.json",
            "pages/prices-live.json",
            "pages/live.json",
        ]
    )
    # Commit only if staged changes exist
    dirty = subprocess.call(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
    if dirty == 0:
        print("[publish] no dashboard changes")
        return 0
    msg = "Auto-update dashboard prices and gainers"
    rc = run(["git", "commit", "-m", msg])
    if rc != 0:
        return 0
    # Always publish docs to main (Pages source). Skip if another push is mid-flight.
    run(["git", "fetch", "origin", "main"])
    rc = run(["git", "push", "origin", "HEAD:main"])
    if rc != 0:
        # one retry after short wait (Pages/git races)
        time.sleep(3)
        run(["git", "pull", "--rebase", "--autostash", "origin", "main"])
        rc = run(["git", "push", "origin", "HEAD:main"])
    print("[publish] done" if rc == 0 else "[publish] push failed")
    return 0  # don't fail cron on push issues


if __name__ == "__main__":
    raise SystemExit(main())

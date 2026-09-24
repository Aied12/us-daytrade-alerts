#!/usr/bin/env python3
"""Rebuild Pages JSON/HTML and push docs/ to GitHub for auto-refresh."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str]) -> int:
    print("+", " ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT)


def main() -> int:
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
            "docs/live.json",
            "pages/index.html",
            "pages/status.json",
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
    # Push main (Pages source)
    branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT, text=True).strip()
    if branch != "main":
        # still push current branch then try main
        run(["git", "push", "-u", "origin", branch])
    rc = run(["git", "push", "origin", "HEAD:main"])
    print("[publish] done" if rc == 0 else "[publish] push failed")
    return 0  # don't fail cron on push issues


if __name__ == "__main__":
    raise SystemExit(main())

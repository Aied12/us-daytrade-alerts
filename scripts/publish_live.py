#!/usr/bin/env python3
"""Refresh only live.json on GitHub Pages (fast price ticks for the dashboard)."""

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
    rc = run([str(py), str(ROOT / "scripts" / "write_live_json.py")])
    if rc != 0:
        return rc
    run(["git", "add", "docs/live.json", "pages/live.json"])
    dirty = subprocess.call(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
    if dirty == 0:
        print("[live] no quote changes")
        return 0
    run(["git", "commit", "-m", "Auto-update live.json quotes"])
    run(["git", "push", "origin", "HEAD:main"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

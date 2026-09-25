#!/usr/bin/env python3
"""Refresh only prices-live.json locally (no git push — combined publisher pushes)."""

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
    print("[live] wrote prices-live.json (push deferred to publish_pages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

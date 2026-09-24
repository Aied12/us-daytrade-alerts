from __future__ import annotations

"""87 Clear error logging + heartbeat status."""

import json
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
LOGS.mkdir(parents=True, exist_ok=True)
ERROR_LOG = LOGS / "errors.log"
STATUS_FILE = ROOT / "data" / "status.json"


def log_error(where: str, err: BaseException | str, detail: str = "") -> None:
    ts = datetime.now(timezone.utc).isoformat()
    if isinstance(err, BaseException):
        msg = f"{type(err).__name__}: {err}"
        tb = traceback.format_exc()
    else:
        msg = str(err)
        tb = detail
    block = f"[{ts}] {where}\n{msg}\n{tb}\n{'-' * 60}\n"
    with ERROR_LOG.open("a", encoding="utf-8") as f:
        f.write(block)
    print(f"[error] {where}: {msg}")


def touch_status(**fields) -> dict:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if STATUS_FILE.exists():
        try:
            data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data.update(fields)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    STATUS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def read_status() -> dict:
    if not STATUS_FILE.exists():
        return {"ok": False, "reason": "لا يوجد status بعد"}
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "reason": str(e)}


def recent_errors(limit: int = 5) -> list[str]:
    if not ERROR_LOG.exists():
        return []
    text = ERROR_LOG.read_text(encoding="utf-8")
    chunks = [c.strip() for c in text.split("-" * 60) if c.strip()]
    return chunks[-limit:]

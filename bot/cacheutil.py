from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 85 thrift / 86 cache / 95 light
LIGHT_MODE = os.getenv("LIGHT_MODE", "0").strip() in ("1", "true", "yes")
API_THRIFT = os.getenv("API_THRIFT", "1" if LIGHT_MODE else "0").strip() in ("1", "true", "yes")
PRICE_CACHE_TTL = int(os.getenv("PRICE_CACHE_TTL", "120" if API_THRIFT or LIGHT_MODE else "45"))


def cache_path(key: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
    return CACHE_DIR / f"px_{safe}.json"


def get_json(key: str, ttl: int | None = None) -> Any | None:
    ttl = PRICE_CACHE_TTL if ttl is None else ttl
    path = cache_path(key)
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age > ttl:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def set_json(key: str, value: Any) -> None:
    path = cache_path(key)
    path.write_text(json.dumps(value, ensure_ascii=False, default=str), encoding="utf-8")


def cached_call(key: str, fn: Callable[[], Any], ttl: int | None = None) -> Any:
    hit = get_json(key, ttl=ttl)
    if hit is not None:
        return hit
    value = fn()
    try:
        set_json(key, value)
    except Exception:
        pass
    return value


def light_watchlist(symbols: list[str]) -> list[str]:
    """95 — smaller universe on weak networks."""
    if not LIGHT_MODE:
        return symbols
    priority = [
        "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "META", "AMZN",
        "AMD", "PLTR", "XOM", "JPM",
    ]
    ordered = [s for s in priority if s in symbols]
    rest = [s for s in symbols if s not in ordered]
    return (ordered + rest)[:12]

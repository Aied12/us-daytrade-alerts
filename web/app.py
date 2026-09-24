#!/usr/bin/env python3
"""82 — Simple mobile-friendly web dashboard."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, abort, request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.backup import mask_token
from bot.config import load_settings
from bot.holidays import holiday_note, is_trading_day
from bot.ops import read_status, recent_errors

app = Flask(__name__)


def _authorized() -> bool:
    settings = load_settings()
    token = settings.dashboard_token
    if not token:
        # if no token configured, allow local-only-ish access but warn
        return True
    q = request.args.get("token") or request.headers.get("X-Dashboard-Token")
    return q == token


@app.get("/")
def home():
    if not _authorized():
        abort(401)
    settings = load_settings()
    st = read_status()
    note = holiday_note() or ""
    trading = "نعم" if is_trading_day() else "لا"
    errs = recent_errors(3)
    err_html = "<br>".join(e.replace("\n", "<br>")[:300] for e in errs) or "لا أخطاء مسجّلة"
    html = f"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>لوحة التنبيهات</title>
<style>
body{{font-family:system-ui,Segoe UI,Tahoma,sans-serif;margin:0;background:#0b1220;color:#e8eefc}}
.wrap{{max-width:720px;margin:0 auto;padding:16px}}
.card{{background:#121a2b;border:1px solid #243047;border-radius:14px;padding:14px;margin:12px 0}}
h1{{font-size:1.25rem;margin:8px 0}}
.muted{{color:#9fb0d0;font-size:.9rem}}
.ok{{color:#4ade80}}.bad{{color:#f87171}}
a.btn{{display:inline-block;margin:6px 4px;padding:10px 14px;background:#1d4ed8;color:#fff;border-radius:10px;text-decoration:none}}
</style></head>
<body><div class="wrap">
<h1>لوحة تنبيهات الأسهم</h1>
<p class="muted">جوال-فرندلي | تعليمي فقط</p>
<div class="card">
<p>البوت: <b>{"شغال" if settings.telegram_enabled else "غير مربوط"}</b></p>
<p>الوضع: {settings.user_mode} | LIGHT={settings.light_mode}</p>
<p>رأس المال: {settings.capital_sar:,.0f} ر.س</p>
<p>التوكن: {mask_token(settings.telegram_bot_token)}</p>
<p>منطقة زمنية: {settings.timezone_name}</p>
<p>يوم تداول أمريكي؟ {trading}</p>
<p class="muted">{note}</p>
</div>
<div class="card">
<h1>الحالة</h1>
<pre style="white-space:pre-wrap">{json.dumps(st, ensure_ascii=False, indent=2)}</pre>
</div>
<div class="card">
<h1>آخر الأخطاء</h1>
<div class="muted">{err_html}</div>
</div>
<div class="card">
<a class="btn" href="/api/status">JSON status</a>
<a class="btn" href="/health">health</a>
</div>
</div></body></html>"""
    return Response(html, mimetype="text/html; charset=utf-8")


@app.get("/health")
def health():
    return {"ok": True, "ts": datetime.utcnow().isoformat()}


@app.get("/api/status")
def api_status():
    if not _authorized():
        abort(401)
    settings = load_settings()
    return {
        "status": read_status(),
        "telegram_enabled": settings.telegram_enabled,
        "mode": settings.user_mode,
        "light_mode": settings.light_mode,
        "timezone": settings.timezone_name,
        "trading_day": is_trading_day(),
        "holiday_note": holiday_note(),
        "users": len(settings.all_private_chat_ids),
    }


if __name__ == "__main__":
    port = int(__import__("os").environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)

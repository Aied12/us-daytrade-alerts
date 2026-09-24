from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from bot.config import Settings
from bot.risk import plan_trade
from bot.signals import Action, Signal

RIYADH = ZoneInfo("Asia/Riyadh")
STATE_FILE = "last_tick_state.json"
NOTIFY_FILE = "notify_gate.json"

# Anti-spam: at most one non-urgent Telegram digest per window
NOTIFY_COOLDOWN_SEC = 20 * 60
# Action flip must persist this many ticks before we announce it
CONFIRM_TICKS = 2


@dataclass
class SignalFinger:
    symbol: str
    action: str
    score: float
    entry: float
    stop: float
    target: float
    change_pct: float | None = None


def _fingerprints(
    settings: Settings,
    signals: list[Signal],
    change_by_symbol: dict[str, float] | None = None,
) -> list[dict]:
    change_by_symbol = change_by_symbol or {}
    out: list[dict] = []
    for s in signals:
        if s.symbol == "MARKET" or s.action in (Action.WAIT, Action.NO_TRADE_DAY):
            continue
        if s.action == Action.CONSIDER_SHORT or getattr(s, "side", None) == "short":
            continue
        if s.action not in (
            Action.CONSIDER_LONG,
            Action.WATCH_ENTRY,
            Action.TAKE_PROFIT_ZONE,
            Action.AVOID,
        ):
            continue
        out.append(
            {
                "symbol": s.symbol,
                "action": s.action.value,
                "score": round(s.score, 1),
                "score_100": getattr(s, "score_100", int(s.score * 10)),
                "strategies": getattr(s, "strategies", [])[:4],
                "entry": round(s.entry_hint, 2),
                "stop": round(s.stop_hint, 2),
                "target": round(s.target_hint, 2),
                "change_pct": round(change_by_symbol.get(s.symbol, 0.0), 2),
            }
        )
    out.sort(key=lambda x: x["symbol"])
    return out


def load_state(settings: Settings) -> dict | None:
    path = settings.data_dir / STATE_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_state(settings: Settings, fingerprints: list[dict], market_tone: str) -> None:
    path = settings.data_dir / STATE_FILE
    payload = {
        "updated_at": datetime.now(RIYADH).isoformat(),
        "market_tone": market_tone,
        "signals": fingerprints,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_gate(settings: Settings) -> dict:
    path = settings.data_dir / NOTIFY_FILE
    if not path.exists():
        return {"last_notify_ts": 0, "pending": {}, "last_hash": ""}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"last_notify_ts": 0, "pending": {}, "last_hash": ""}


def _save_gate(settings: Settings, gate: dict) -> None:
    path = settings.data_dir / NOTIFY_FILE
    path.write_text(json.dumps(gate, ensure_ascii=False, indent=2), encoding="utf-8")


def _by_symbol(items: list[dict]) -> dict[str, dict]:
    return {i["symbol"]: i for i in items}


def _is_short_fp(fp: dict) -> bool:
    return "بيع قصير" in str(fp.get("action") or "")


def _is_watch_fp(fp: dict) -> bool:
    return "راقب" in str(fp.get("action") or "")


def _is_long_fp(fp: dict) -> bool:
    return "شراء" in str(fp.get("action") or "")


def _band(fp: dict) -> str:
    """Collapse long/watch into one band so PLTR flip doesn't spam."""
    if _is_long_fp(fp):
        return "long"
    if _is_watch_fp(fp):
        return "watch"
    return "other"


def _confirm(gate: dict, key: str) -> bool:
    """Return True only when the same pending event seen CONFIRM_TICKS times."""
    pending = gate.setdefault("pending", {})
    item = pending.get(key) or {"count": 0}
    item["count"] = int(item.get("count") or 0) + 1
    pending[key] = item
    if item["count"] >= CONFIRM_TICKS:
        pending.pop(key, None)
        return True
    return False


def _clear_pending_prefix(gate: dict, prefix: str) -> None:
    pending = gate.setdefault("pending", {})
    for k in list(pending):
        if k.startswith(prefix):
            pending.pop(k, None)


def describe_changes(
    settings: Settings,
    prev: dict | None,
    fingerprints: list[dict],
    signals: list[Signal],
    market_tone: str,
    gate: dict,
) -> tuple[bool, str, bool]:
    """Return (changed, body, is_urgent)."""
    now = datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M")
    urgent = False

    if prev is None:
        lines = [
            f"⏰ {now} (السعودية)",
            f"📊 مزاج السوق: {market_tone}",
            "",
            "أول تحديث للفترة — ملخص الفرص الحالية:",
        ]
        if not fingerprints:
            lines.append("لا توجد إشارات قوية الآن.")
        else:
            for fp in fingerprints[:8]:
                lines.append(
                    f"• {fp['symbol']}: {fp['action']} "
                    f"({fp['change_pct']:+.1f}%) "
                    f"دخول≈${fp['entry']} وقف≈${fp['stop']} هدف≈${fp['target']}"
                )
                if _is_long_fp(fp) and float(fp.get("score_100") or 0) >= 70:
                    urgent = True
        return True, "\n".join(lines), urgent

    prev_list = [s for s in (prev.get("signals") or []) if not _is_short_fp(s)]
    prev_sigs = _by_symbol(prev_list)
    curr_sigs = _by_symbol(fingerprints)
    changes: list[str] = []

    # Tone: only if major wording change and confirmed — skip alone as spam
    # (tone still shown at bottom of digest when we do send)

    for sym, cur in curr_sigs.items():
        old = prev_sigs.get(sym)
        if old is None:
            # New watch: ignore (too noisy). New long: confirm then announce.
            if _is_watch_fp(cur):
                continue
            if _is_long_fp(cur):
                key = f"new_long:{sym}"
                if float(cur.get("score_100") or 0) >= 70 or _confirm(gate, key):
                    _clear_pending_prefix(gate, f"gone_long:{sym}")
                    changes.append(
                        f"🆕 {sym}: فرصة شراء «{cur['action']}» "
                        f"({cur['change_pct']:+.1f}%) "
                        f"دخول≈${cur['entry']} وقف≈${cur['stop']} هدف≈${cur['target']}"
                    )
                    if float(cur.get("score_100") or 0) >= 70:
                        urgent = True
            continue

        _clear_pending_prefix(gate, f"gone_long:{sym}")
        _clear_pending_prefix(gate, f"new_long:{sym}")

        old_b, cur_b = _band(old), _band(cur)
        # Ignore long↔watch flip entirely (main source of spam for PLTR)
        if {old_b, cur_b} <= {"long", "watch"} and old_b != cur_b:
            continue

        bits = []
        if old.get("action") != cur["action"] and old_b == cur_b:
            bits.append(f"الإشارة: {old['action']} ← {cur['action']}")
        # Only big level moves
        if old_b == "long" and abs(float(old.get("entry", 0)) - float(cur["entry"])) >= 1.5:
            bits.append(f"الدخول: ${old['entry']} ← ${cur['entry']}")
        if bits:
            changes.append(f"✏️ {sym}: " + " | ".join(bits))

    for sym, old in prev_sigs.items():
        if sym in curr_sigs:
            continue
        if not _is_long_fp(old):
            continue
        key = f"gone_long:{sym}"
        if _confirm(gate, key):
            changes.append(f"❌ {sym}: اختفت فرصة الشراء بعد تأكيد فحصين")

    if not changes:
        return False, "لا يوجد شي جديد يابطل", False

    sig_map = {s.symbol: s for s in signals}
    extra = []
    for line in changes:
        if not line.startswith("🆕"):
            continue
        sym = line.split()[1].rstrip(":")
        sig = sig_map.get(sym)
        if sig and sig.action == Action.CONSIDER_LONG:
            plan = plan_trade(settings, sig)
            extra.append(
                f"ماذا تفعل في {sym}: {plan.action_ar} | "
                f"{plan.shares} سهم | مخاطرة≈{plan.risk_sar:.0f} ر.س"
            )

    board = ["", "📋 الفرص الحالية على اللوحة:"]
    if fingerprints:
        for fp in fingerprints[:8]:
            board.append(f"• {fp['symbol']}: {fp['action']} ({fp['change_pct']:+.1f}%)")
    else:
        board.append("• لا توجد فرص شراء/مراقبة الآن")

    body = [
        f"⏰ {now} (السعودية)",
        "فيه تغيّر مهم:",
        "",
        *changes[:10],
    ]
    if extra:
        body.append("")
        body.extend(extra[:5])
    body.extend(board)
    body.append("")
    body.append(f"📊 مزاج السوق الآن: {market_tone}")
    body.append("⏱ التنبيهات مهدّأة — المهم فقط كل فترة")
    return True, "\n".join(body), urgent


def build_tick_message(
    settings: Settings,
    signals: list[Signal],
    change_by_symbol: dict[str, float],
    market_tone: str,
) -> tuple[bool, str]:
    fps = _fingerprints(settings, signals, change_by_symbol)
    prev = load_state(settings)
    gate = _load_gate(settings)
    changed, body, urgent = describe_changes(settings, prev, fps, signals, market_tone, gate)

    # Always refresh dashboard state
    save_state(settings, fps, market_tone)

    if not changed:
        _save_gate(settings, gate)
        return False, body

    digest = hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]
    now_ts = time.time()
    last_ts = float(gate.get("last_notify_ts") or 0)
    last_hash = gate.get("last_hash") or ""

    # Drop identical repeats
    if digest == last_hash:
        _save_gate(settings, gate)
        return False, body

    # Cooldown unless urgent new/strong long
    if not urgent and (now_ts - last_ts) < NOTIFY_COOLDOWN_SEC:
        _save_gate(settings, gate)
        print(
            f"[notify] suppressed (cooldown {int(NOTIFY_COOLDOWN_SEC - (now_ts - last_ts))}s left)"
        )
        return False, body

    gate["last_notify_ts"] = now_ts
    gate["last_hash"] = digest
    _save_gate(settings, gate)
    return True, body

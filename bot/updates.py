from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.config import Settings
from bot.risk import plan_trade
from bot.signals import Action, Signal

RIYADH = ZoneInfo("Asia/Riyadh")
STATE_FILE = "last_tick_state.json"


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
        if s.action in (Action.WAIT,):
            continue
        out.append(
            {
                "symbol": s.symbol,
                "action": s.action.value,
                "score": round(s.score, 1),
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


def _by_symbol(items: list[dict]) -> dict[str, dict]:
    return {i["symbol"]: i for i in items}


def describe_changes(
    settings: Settings,
    prev: dict | None,
    fingerprints: list[dict],
    signals: list[Signal],
    market_tone: str,
) -> tuple[bool, str]:
    """Return (changed, message_body)."""
    now = datetime.now(RIYADH).strftime("%Y-%m-%d %H:%M")
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
        return True, "\n".join(lines)

    prev_sigs = _by_symbol(prev.get("signals") or [])
    curr_sigs = _by_symbol(fingerprints)
    changes: list[str] = []

    prev_tone = prev.get("market_tone")
    if prev_tone and prev_tone != market_tone:
        changes.append(f"مزاج السوق تغيّر: {prev_tone} ← {market_tone}")

    for sym, cur in curr_sigs.items():
        old = prev_sigs.get(sym)
        if old is None:
            changes.append(
                f"🆕 {sym}: ظهرت إشارة «{cur['action']}» "
                f"({cur['change_pct']:+.1f}%) "
                f"دخول≈${cur['entry']} وقف≈${cur['stop']} هدف≈${cur['target']}"
            )
            continue
        bits = []
        if old.get("action") != cur["action"]:
            bits.append(f"الإجراء: {old['action']} ← {cur['action']}")
        if abs(float(old.get("entry", 0)) - float(cur["entry"])) >= 0.5:
            bits.append(f"الدخول: ${old['entry']} ← ${cur['entry']}")
        if abs(float(old.get("stop", 0)) - float(cur["stop"])) >= 0.4:
            bits.append(f"الوقف: ${old['stop']} ← ${cur['stop']}")
        if abs(float(old.get("target", 0)) - float(cur["target"])) >= 0.5:
            bits.append(f"الهدف: ${old['target']} ← ${cur['target']}")
        old_chg = float(old.get("change_pct") or 0)
        if abs(old_chg - float(cur["change_pct"])) >= 0.4:
            bits.append(f"التغيّر اليومي: {old_chg:+.1f}% ← {cur['change_pct']:+.1f}%")
        if abs(float(old.get("score", 0)) - float(cur["score"])) >= 1.0:
            bits.append(f"قوة الإشارة: {old['score']} ← {cur['score']}")
        if bits:
            changes.append(f"✏️ {sym}: " + " | ".join(bits))

    for sym, old in prev_sigs.items():
        if sym not in curr_sigs:
            changes.append(f"❌ {sym}: اختفت إشارة «{old.get('action')}»")

    if not changes:
        return False, "لا يوجد شي جديد يابطل"

    # Enrich with plan for newly actionable names
    sig_map = {s.symbol: s for s in signals}
    extra = []
    for sym, cur in curr_sigs.items():
        old = prev_sigs.get(sym)
        if old is None or old.get("action") != cur["action"]:
            sig = sig_map.get(sym)
            if sig and sig.action in (Action.CONSIDER_LONG, Action.WATCH_ENTRY):
                plan = plan_trade(settings, sig)
                extra.append(
                    f"ماذا تفعل في {sym}: {plan.action_ar} | "
                    f"{plan.shares} سهم | مخاطرة≈{plan.risk_sar:.0f} ر.س"
                )

    body = [
        f"⏰ {now} (السعودية)",
        "فيه تغيّر:",
        "",
        *changes[:15],
    ]
    if extra:
        body.append("")
        body.extend(extra[:5])
    body.append("")
    body.append(f"📊 مزاج السوق الآن: {market_tone}")
    return True, "\n".join(body)


def build_tick_message(
    settings: Settings,
    signals: list[Signal],
    change_by_symbol: dict[str, float],
    market_tone: str,
) -> tuple[bool, str]:
    fps = _fingerprints(settings, signals, change_by_symbol)
    prev = load_state(settings)
    changed, body = describe_changes(settings, prev, fps, signals, market_tone)
    save_state(settings, fps, market_tone)
    return changed, body

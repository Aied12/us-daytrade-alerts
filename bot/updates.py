from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
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
        if s.symbol == "MARKET" or s.action in (Action.WAIT, Action.NO_TRADE_DAY):
            continue
        # Never track/notify short sells
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


def _by_symbol(items: list[dict]) -> dict[str, dict]:
    return {i["symbol"]: i for i in items}


def _is_short_fp(fp: dict) -> bool:
    action = str(fp.get("action") or "")
    return "بيع قصير" in action


def _is_watch_fp(fp: dict) -> bool:
    return "راقب" in str(fp.get("action") or "")


def _is_long_fp(fp: dict) -> bool:
    return "شراء" in str(fp.get("action") or "")


def describe_changes(
    settings: Settings,
    prev: dict | None,
    fingerprints: list[dict],
    signals: list[Signal],
    market_tone: str,
) -> tuple[bool, str]:
    """Meaningful changes only — match what the dashboard shows."""
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

    # Ignore historical shorts so hiding them never looks like "disappeared"
    prev_list = [s for s in (prev.get("signals") or []) if not _is_short_fp(s)]
    prev_sigs = _by_symbol(prev_list)
    curr_sigs = _by_symbol(fingerprints)
    changes: list[str] = []

    prev_tone = prev.get("market_tone")
    if prev_tone and prev_tone != market_tone:
        changes.append(f"مزاج السوق تغيّر: {prev_tone} ← {market_tone}")

    for sym, cur in curr_sigs.items():
        old = prev_sigs.get(sym)
        if old is None:
            prefix = "🆕" if _is_long_fp(cur) else "👀"
            changes.append(
                f"{prefix} {sym}: ظهرت إشارة «{cur['action']}» "
                f"({cur['change_pct']:+.1f}%) "
                f"دخول≈${cur['entry']} وقف≈${cur['stop']} هدف≈${cur['target']}"
            )
            continue
        bits = []
        if old.get("action") != cur["action"]:
            bits.append(f"الإشارة: {old['action']} ← {cur['action']}")
        if abs(float(old.get("entry", 0)) - float(cur["entry"])) >= 0.8:
            bits.append(f"الدخول: ${old['entry']} ← ${cur['entry']}")
        if abs(float(old.get("stop", 0)) - float(cur["stop"])) >= 0.6:
            bits.append(f"الوقف: ${old['stop']} ← ${cur['stop']}")
        if abs(float(old.get("target", 0)) - float(cur["target"])) >= 0.8:
            bits.append(f"الهدف: ${old['target']} ← ${cur['target']}")
        # Skip tiny % noise that was causing false Telegram vs page mismatch
        if abs(float(old.get("score", 0)) - float(cur["score"])) >= 1.5:
            bits.append(f"قوة الإشارة: {old['score']} ← {cur['score']}")
        if bits:
            changes.append(f"✏️ {sym}: " + " | ".join(bits))

    for sym, old in prev_sigs.items():
        if sym in curr_sigs:
            continue
        if _is_short_fp(old) or _is_watch_fp(old):
            continue  # don't spam "اختفت" for watches / shorts
        if _is_long_fp(old):
            changes.append(f"❌ {sym}: اختفت فرصة الشراء «{old.get('action')}»")

    if not changes:
        return False, "لا يوجد شي جديد يابطل"

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

    board = ["", "📋 الفرص الحالية على اللوحة:"]
    if fingerprints:
        for fp in fingerprints[:8]:
            board.append(f"• {fp['symbol']}: {fp['action']} ({fp['change_pct']:+.1f}%)")
    else:
        board.append("• لا توجد فرص شراء/مراقبة الآن")

    body = [
        f"⏰ {now} (السعودية)",
        "فيه تغيّر:",
        "",
        *changes[:12],
    ]
    if extra:
        body.append("")
        body.extend(extra[:5])
    body.extend(board)
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

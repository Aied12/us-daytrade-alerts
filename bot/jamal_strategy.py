"""استراتيجية جمال — Setup → Entry Trigger → Stop/TP → Exit (day-trade).

Does NOT fire an entry on indicators alone. Requires a clear price Trigger
(break of short resistance + buffer) with Volume/MFI confirmation.

Reuses bot.risk sizing helpers. Order-book support is optional when present.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, time as dtime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from bot.config import Settings
from bot.market_data import QuoteSnapshot, fetch_history

ROOT = Path(__file__).resolve().parent.parent
BOARD_PATH = ROOT / "data" / "jamal_board.json"
NY = ZoneInfo("America/New_York")


class JamalState(str, Enum):
    WATCH = "WATCH"
    ENTRY_TRIGGERED = "ENTRY_TRIGGERED"
    TP1_HIT = "TP1_HIT"
    TP2_HIT = "TP2_HIT"
    STOPPED = "STOPPED"
    EXPIRED = "EXPIRED"
    NO_CHASE = "NO_CHASE"
    EXIT_BEFORE_CLOSE = "EXIT_BEFORE_CLOSE"


STATE_AR = {
    JamalState.WATCH: "🟡 مراقبة",
    JamalState.ENTRY_TRIGGERED: "🟢 دخول محتمل",
    JamalState.TP1_HIT: "🔵 TP1 Hit",
    JamalState.TP2_HIT: "🟣 TP2 Hit",
    JamalState.STOPPED: "🔴 Stop Loss",
    JamalState.EXPIRED: "⚪ Signal Expired",
    JamalState.NO_CHASE: "⚪ لا تطارد",
    JamalState.EXIT_BEFORE_CLOSE: "⚫ خروج قبل الإغلاق",
}


@dataclass
class JamalSettings:
    buffer_pct: float = 0.10          # Entry = short_high * (1 + buffer/100)
    max_chase_pct: float = 1.0        # فوق Entry بهذا القدر → NO_CHASE
    stop_loss_pct: float = 2.0        # احتياطي إن لم يتوفر دعم
    tp1_rr: float = 1.5
    tp2_rr: float = 2.5
    trailing_enabled: bool = True
    trailing_pct: float = 1.0
    signal_expiry_min: float = 15.0
    exit_before_close_min: float = 15.0
    vol_ratio_min: float = 1.20
    mfi_min: float = 50.0
    risk_pct: float = 1.0             # لحجم الصفقة (٪ من رأس المال)
    score_max: int = 90


DEFAULT_JAMAL = JamalSettings()


def _px(n: float) -> float:
    n = float(n or 0)
    if n <= 0:
        return 0.0
    return round(n, 4 if n < 1 else 2)


def compute_mfi(df: pd.DataFrame, period: int = 14) -> tuple[float, float]:
    """Return (mfi_now, mfi_prev). Falls back to (50, 50) if insufficient data."""
    if df is None or df.empty or len(df) < period + 2:
        return 50.0, 50.0
    high, low, close, vol = df["High"], df["Low"], df["Close"], df["Volume"]
    tp = (high + low + close) / 3.0
    rmf = tp * vol
    delta = tp.diff()
    pos = rmf.where(delta > 0, 0.0)
    neg = rmf.where(delta < 0, 0.0)
    pos_sum = pos.rolling(period).sum()
    neg_sum = neg.rolling(period).sum().replace(0, pd.NA)
    mfi = 100 - (100 / (1 + pos_sum / neg_sum))
    now = float(mfi.iloc[-1]) if pd.notna(mfi.iloc[-1]) else 50.0
    prev = float(mfi.iloc[-2]) if pd.notna(mfi.iloc[-2]) else now
    return now, prev


def size_long_position(
    *,
    capital_usd: float,
    risk_pct: float,
    entry: float,
    stop: float,
) -> dict[str, Any]:
    """Reuse-friendly position sizing (same idea as حاسبة الاستراتيجية / plan_trade)."""
    entry = float(entry or 0)
    stop = float(stop or 0)
    capital = max(float(capital_usd or 0), 0.0)
    risk_pct = max(float(risk_pct or 0), 0.0)
    risk_budget = capital * (risk_pct / 100.0)
    risk_ps = max(entry - stop, 0.0)
    if entry <= 0 or risk_ps <= 0 or risk_budget <= 0:
        return {
            "shares": 0,
            "position_usd": 0.0,
            "risk_usd": 0.0,
            "max_shares_by_capital": 0,
            "allowed": False,
            "note_ar": "لا يمكن حساب الحجم — تحقق من Entry/Stop/رأس المال",
        }
    shares = int(risk_budget // risk_ps)
    max_by_cap = int(capital // entry) if entry else 0
    if shares > max_by_cap:
        shares = max_by_cap
    position_usd = round(shares * entry, 2)
    risk_usd = round(shares * risk_ps, 2)
    return {
        "shares": shares,
        "position_usd": position_usd,
        "risk_usd": risk_usd,
        "max_shares_by_capital": max_by_cap,
        "allowed": shares >= 1,
        "note_ar": (
            f"{shares} سهم · صفقة ${position_usd} · مخاطرة ${risk_usd}"
            if shares >= 1
            else "رأس المال/المخاطرة لا تكفي لسهم واحد"
        ),
    }


def _load_board() -> dict[str, Any]:
    try:
        data = json.loads(BOARD_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_board(board: dict[str, Any]) -> None:
    try:
        BOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
        items = sorted(
            board.items(),
            key=lambda kv: float((kv[1] or {}).get("updated_ts") or 0),
            reverse=True,
        )[:40]
        BOARD_PATH.write_text(
            json.dumps(dict(items), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _minutes_to_close(now: datetime | None = None) -> float | None:
    now = now or datetime.now(NY)
    if now.tzinfo is None:
        now = now.replace(tzinfo=NY)
    else:
        now = now.astimezone(NY)
    close = datetime.combine(now.date(), dtime(16, 0), tzinfo=NY)
    # only meaningful during RTH
    open_t = datetime.combine(now.date(), dtime(9, 30), tzinfo=NY)
    if now < open_t or now > close:
        return None
    return max(0.0, (close - now).total_seconds() / 60.0)


def _short_resistance(snap: QuoteSnapshot) -> float:
    cands = [
        float(snap.day_high or 0),
        float(snap.resistance or 0),
        float(snap.high_20 or 0),
    ]
    above = [c for c in cands if c > float(snap.last or 0)]
    if above:
        return min(above)
    # fallback: day high even if last ≈ high
    return max(float(snap.day_high or snap.last or 0), float(snap.last or 0))


def _short_support(snap: QuoteSnapshot) -> float:
    cands = [
        float(snap.day_low or 0),
        float(snap.support or 0),
        float(snap.low_20 or 0),
    ]
    below = [c for c in cands if 0 < c < float(snap.last or 0)]
    if below:
        return max(below)
    return float(snap.day_low or snap.last or 0) * 0.98


def evaluate_jamal_setup(
    snap: QuoteSnapshot,
    *,
    settings: JamalSettings | None = None,
    order_book: dict[str, Any] | None = None,
    hist: pd.DataFrame | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Build/update one Jamal card for a snapshot. Returns None if out of universe."""
    cfg = settings or DEFAULT_JAMAL
    now = now or datetime.now(NY)
    if now.tzinfo is None:
        now = now.replace(tzinfo=NY)
    else:
        now = now.astimezone(NY)

    last = float(snap.last or 0)
    if last < 5:  # day-trade liquid board; Jamal uses same floor as main board
        return None

    if hist is None or hist.empty:
        try:
            hist = fetch_history(snap.symbol, period="3mo", interval="1d")
        except Exception:
            hist = pd.DataFrame()

    mfi_now, mfi_prev = compute_mfi(hist) if hist is not None and not hist.empty else (50.0, 50.0)
    vol_ratio = float(snap.volume / snap.avg_volume_20) if snap.avg_volume_20 else 0.0
    mfi_rising = mfi_now >= mfi_prev
    short_high = _short_resistance(snap)
    short_low = _short_support(snap)
    entry_trigger = _px(short_high * (1.0 + cfg.buffer_pct / 100.0))

    # Order book optional
    ob_ok = None
    ob_note = "Order Book غير متاح"
    if order_book and order_book.get("bid_ask_ratio") is not None:
        ratio = float(order_book.get("bid_ask_ratio") or 0)
        ob_ok = ratio >= 1.2
        ob_note = f"Bid/Ask {ratio:.2f}"

    checks = [
        {"key": "vol", "ok": vol_ratio >= cfg.vol_ratio_min, "ar": f"Volume {vol_ratio:.2f}x VMA", "need": True},
        {"key": "mfi", "ok": mfi_now >= cfg.mfi_min, "ar": f"MFI {mfi_now:.1f}", "need": True},
        {"key": "mfi_up", "ok": mfi_rising, "ar": "MFI صاعد ↑" if mfi_rising else "MFI غير صاعد", "need": True},
        {
            "key": "breakout_level",
            "ok": short_high > 0,
            "ar": f"أقرب قمة قصيرة ${ _px(short_high) }",
            "need": True,
        },
        {
            "key": "above_ma",
            "ok": bool(snap.ma20 and last >= snap.ma20),
            "ar": "السعر فوق المتوسط القصير" if (snap.ma20 and last >= snap.ma20) else "تحت المتوسط القصير",
            "need": False,
        },
    ]
    if ob_ok is not None:
        checks.append({"key": "orderbook", "ok": bool(ob_ok), "ar": ob_note, "need": False})

    need_ok = all(c["ok"] for c in checks if c.get("need"))
    score = int(round(sum(1 for c in checks if c["ok"]) / max(len(checks), 1) * cfg.score_max))

    board = _load_board()
    prev = board.get(snap.symbol.upper()) if isinstance(board.get(snap.symbol.upper()), dict) else {}

    # Freeze entry trigger once created (until expired / new setup)
    created_ts = float(prev.get("created_ts") or now.timestamp())
    frozen_entry = float(prev.get("entry_trigger") or 0)
    if frozen_entry > 0 and prev.get("state") not in (
        JamalState.EXPIRED.value,
        JamalState.STOPPED.value,
        JamalState.TP2_HIT.value,
        JamalState.EXIT_BEFORE_CLOSE.value,
    ):
        entry_trigger = _px(frozen_entry)
    else:
        created_ts = now.timestamp()

    # Stop: prefer short support under entry; else %
    stop_from_sup = _px(short_low)
    stop_from_pct = _px(entry_trigger * (1.0 - cfg.stop_loss_pct / 100.0))
    if stop_from_sup > 0 and stop_from_sup < entry_trigger:
        stop = stop_from_sup
        # don't allow absurdly wide stop (> 2× pct stop distance)
        if entry_trigger - stop > (entry_trigger - stop_from_pct) * 2:
            stop = stop_from_pct
    else:
        stop = stop_from_pct
    if stop >= entry_trigger:
        stop = stop_from_pct

    risk = max(entry_trigger - stop, 0.0)
    tp1 = _px(entry_trigger + risk * cfg.tp1_rr)
    tp2 = _px(entry_trigger + risk * cfg.tp2_rr)

    age_min = max(0.0, (now.timestamp() - created_ts) / 60.0)
    mins_to_close = _minutes_to_close(now)

    breakout_hit = last >= entry_trigger and need_ok
    chase = entry_trigger > 0 and last > entry_trigger * (1.0 + cfg.max_chase_pct / 100.0)

    # State machine (single state)
    prev_state = str(prev.get("state") or "")
    state = JamalState.WATCH

    if mins_to_close is not None and mins_to_close <= cfg.exit_before_close_min and prev_state in (
        JamalState.ENTRY_TRIGGERED.value,
        JamalState.TP1_HIT.value,
        JamalState.WATCH.value,
    ):
        if prev_state in (JamalState.ENTRY_TRIGGERED.value, JamalState.TP1_HIT.value):
            state = JamalState.EXIT_BEFORE_CLOSE
        elif age_min >= cfg.signal_expiry_min and not breakout_hit:
            state = JamalState.EXPIRED
        else:
            state = JamalState.WATCH
    elif age_min >= cfg.signal_expiry_min and not breakout_hit and prev_state not in (
        JamalState.ENTRY_TRIGGERED.value,
        JamalState.TP1_HIT.value,
        JamalState.TP2_HIT.value,
    ):
        state = JamalState.EXPIRED
    elif chase and not (
        prev_state in (JamalState.ENTRY_TRIGGERED.value, JamalState.TP1_HIT.value, JamalState.TP2_HIT.value)
        and last >= entry_trigger
    ):
        # فقط قبل التفعيل: منع المطاردة
        if prev_state not in (JamalState.ENTRY_TRIGGERED.value, JamalState.TP1_HIT.value, JamalState.TP2_HIT.value):
            state = JamalState.NO_CHASE
        else:
            state = JamalState(prev_state) if prev_state in JamalState._value2member_map_ else JamalState.ENTRY_TRIGGERED
    elif need_ok and breakout_hit and not chase:
        state = JamalState.ENTRY_TRIGGERED
    elif need_ok:
        state = JamalState.WATCH
    else:
        state = JamalState.WATCH

    # After entry: manage hits / exit signals (do not move entry)
    in_trade = state in (JamalState.ENTRY_TRIGGERED, JamalState.TP1_HIT) or prev_state in (
        JamalState.ENTRY_TRIGGERED.value,
        JamalState.TP1_HIT.value,
    )
    if in_trade and state not in (JamalState.EXPIRED, JamalState.NO_CHASE, JamalState.EXIT_BEFORE_CLOSE):
        if last <= stop:
            state = JamalState.STOPPED
        elif last >= tp2:
            state = JamalState.TP2_HIT
        elif last >= tp1:
            state = JamalState.TP1_HIT
        else:
            # Exit Signal (soft)
            exit_signal = False
            exit_reason = ""
            if mfi_now < cfg.mfi_min and prev_state in (
                JamalState.ENTRY_TRIGGERED.value,
                JamalState.TP1_HIT.value,
            ):
                exit_signal = True
                exit_reason = "Exit Signal: MFI تحت 50 بعد الدخول"
            elif not mfi_rising and mfi_now < mfi_prev - 8:
                exit_signal = True
                exit_reason = "Exit Signal: انعكاس MFI"
            elif vol_ratio < 0.85:
                exit_signal = True
                exit_reason = "Exit Signal: فقدان زخم الحجم"
            elif short_low and last < short_low:
                exit_signal = True
                exit_reason = "Exit Signal: كسر الدعم"
            elif ob_ok is False:
                exit_signal = True
                exit_reason = "Exit Signal: ضغط بيع في Order Book"
            if exit_signal and state == JamalState.ENTRY_TRIGGERED:
                # keep ENTRY but annotate; user asked exit types — use STOPPED-like soft exit label via reason
                pass
            # Trailing after TP1
            if state == JamalState.TP1_HIT and cfg.trailing_enabled:
                trail = _px(last * (1.0 - cfg.trailing_pct / 100.0))
                if trail > stop:
                    stop = trail
                    risk = max(entry_trigger - stop, 0.0)

    cancel_reason = ""
    if state == JamalState.NO_CHASE:
        cancel_reason = "فاتت نقطة الدخول — انتظر إعادة الاختبار"
    elif state == JamalState.EXPIRED:
        cancel_reason = f"انتهت صلاحية الإشارة ({cfg.signal_expiry_min:.0f} د بدون Trigger)"
    elif state == JamalState.EXIT_BEFORE_CLOSE:
        cancel_reason = f"إغلاق الصفقة قبل نهاية الجلسة ({cfg.exit_before_close_min:.0f} د)"
    elif state == JamalState.STOPPED:
        cancel_reason = "Stop Loss Hit"
    elif not need_ok and state == JamalState.WATCH:
        cancel_reason = "Setup غير مكتمل — بانتظار Volume/MFI/قمة"

    sizing = size_long_position(
        capital_usd=0,  # filled by caller with settings
        risk_pct=cfg.risk_pct,
        entry=entry_trigger,
        stop=stop,
    )

    watch_clock = datetime.fromtimestamp(created_ts, tz=NY).strftime("%H:%M:%S")
    trigger_clock = now.strftime("%H:%M:%S") if state == JamalState.ENTRY_TRIGGERED else "—"
    hold_ar = f"قبل إغلاق السوق بـ {cfg.exit_before_close_min:.0f} دقيقة"

    card = {
        "symbol": snap.symbol.upper(),
        "strategy": "jamal",
        "strategy_ar": "استراتيجية جمال",
        "tag_ar": "🎯 استراتيجية جمال",
        "last": _px(last),
        "score": score,
        "score_max": cfg.score_max,
        "state": state.value,
        "state_ar": STATE_AR[state],
        "signal_ts": int(created_ts),
        "signal_clock_et": watch_clock,
        "trigger_clock_et": trigger_clock,
        "age_min": round(age_min, 1),
        "watch_since_ar": f"وقت مراقبة السهم: {watch_clock} ET",
        "trigger_time_ar": f"وقت تفعيل الدخول: {trigger_clock} ET",
        "age_ar": f"الوقت المنقضي منذ الإشارة: {int(age_min)} د",
        "entry": entry_trigger,
        "entry_trigger": entry_trigger,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "risk_ps": _px(risk),
        "rr_ar": f"1 : {cfg.tp1_rr} / 1 : {cfg.tp2_rr}",
        "tp1_rr": cfg.tp1_rr,
        "tp2_rr": cfg.tp2_rr,
        "exit_before_close_ar": hold_ar,
        "hold_ar": hold_ar,
        "cancel_reason_ar": cancel_reason,
        "checks": checks,
        "checks_ar": [f"{'✓' if c['ok'] else '✗'} {c['ar']}" for c in checks],
        "vol_ratio": round(vol_ratio, 2),
        "mfi": round(mfi_now, 1),
        "mfi_prev": round(mfi_prev, 1),
        "mfi_rising": mfi_rising,
        "short_high": _px(short_high),
        "short_low": _px(short_low),
        "buffer_pct": cfg.buffer_pct,
        "max_chase_pct": cfg.max_chase_pct,
        "stop_loss_pct": cfg.stop_loss_pct,
        "trailing_enabled": cfg.trailing_enabled,
        "trailing_pct": cfg.trailing_pct,
        "signal_expiry_min": cfg.signal_expiry_min,
        "exit_before_close_min": cfg.exit_before_close_min,
        "order_book_available": ob_ok is not None,
        "order_book_ok": ob_ok,
        "order_book_note_ar": ob_note,
        "mins_to_close": mins_to_close,
        "sizing": sizing,
        "disclaimer_ar": "Setup / Entry Trigger / Stop / Target — لأغراض تعليمية وليست توصية استثمارية",
        "updated_ts": int(now.timestamp()),
        "created_ts": int(created_ts),
    }

    # Persist frozen fields
    board[snap.symbol.upper()] = {
        "created_ts": created_ts,
        "entry_trigger": entry_trigger,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "state": state.value,
        "updated_ts": now.timestamp(),
        "card": card,
    }
    _save_board(board)
    return card


def attach_sizing(card: dict[str, Any], settings: Settings, jamal: JamalSettings | None = None) -> dict[str, Any]:
    cfg = jamal or DEFAULT_JAMAL
    sizing = size_long_position(
        capital_usd=settings.capital_usd,
        risk_pct=cfg.risk_pct * 100 if cfg.risk_pct <= 1 else cfg.risk_pct,
        # risk_pct in JamalSettings is percent points (1.0 = 1%); Settings uses fraction
        entry=float(card.get("entry") or 0),
        stop=float(card.get("stop") or 0),
    )
    # Fix: JamalSettings.risk_pct is 1.0 meaning 1% — size_long expects percent points
    sizing = size_long_position(
        capital_usd=settings.capital_usd,
        risk_pct=float(cfg.risk_pct),
        entry=float(card.get("entry") or 0),
        stop=float(card.get("stop") or 0),
    )
    card = dict(card)
    card["sizing"] = sizing
    card["shares"] = sizing["shares"]
    card["position_usd"] = sizing["position_usd"]
    card["risk_usd"] = sizing["risk_usd"]
    return card


def build_jamal_scanner(
    snaps: list[QuoteSnapshot],
    *,
    settings: Settings,
    jamal: JamalSettings | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    cfg = jamal or DEFAULT_JAMAL
    out: list[dict[str, Any]] = []
    for snap in snaps:
        if not snap or snap.symbol in ("SPY", "QQQ", "IWM"):
            continue
        try:
            card = evaluate_jamal_setup(snap, settings=cfg)
        except Exception:
            continue
        if not card:
            continue
        # keep names that are at least forming a setup (score gate soft)
        if int(card.get("score") or 0) < 30 and card.get("state") == JamalState.WATCH.value:
            # still include if volume hot
            if float(card.get("vol_ratio") or 0) < cfg.vol_ratio_min:
                continue
        card = attach_sizing(card, settings, cfg)
        out.append(card)
    out.sort(
        key=lambda c: (
            0 if c.get("state") == JamalState.ENTRY_TRIGGERED.value else 1,
            0 if c.get("state") == JamalState.WATCH.value else 2,
            -int(c.get("score") or 0),
            -float(c.get("vol_ratio") or 0),
        )
    )
    return out[:limit]


def jamal_to_opportunity(card: dict[str, Any]) -> dict[str, Any]:
    """Map Jamal card into خطط الدخول shape (reuse UI)."""
    state = str(card.get("state") or "")
    if state == JamalState.ENTRY_TRIGGERED.value:
        action = "فكّر في شراء قصير المدى"
        action_key = "CONSIDER_LONG"
        allowed = True
    elif state == JamalState.WATCH.value:
        action = "راقب دخول"
        action_key = "WATCH_ENTRY"
        allowed = False
    else:
        action = "انتظر تأكيد"
        action_key = "WAIT"
        allowed = False
    return {
        "symbol": card.get("symbol"),
        "action": action,
        "action_key": action_key,
        "side": "long",
        "score_100": int(card.get("score") or 0),
        "confidence": "A" if state == JamalState.ENTRY_TRIGGERED.value else "B",
        "confidence_ar": card.get("state_ar"),
        "urgent": state == JamalState.ENTRY_TRIGGERED.value,
        "strategies": ["استراتيجية جمال"],
        "reason": " · ".join(card.get("checks_ar") or [])[:220],
        "entry": card.get("entry"),
        "entry_original": card.get("entry"),
        "entry_planned": card.get("entry"),
        "stop": card.get("stop"),
        "target": card.get("tp1"),
        "target_partial": card.get("tp1"),
        "target_final": card.get("tp2"),
        "shares": card.get("shares") or 0,
        "risk_usd": card.get("risk_usd") or 0,
        "allowed": allowed and bool(card.get("shares")),
        "change_pct": 0,
        "last": card.get("last"),
        "tag_ar": "🎯 استراتيجية جمال",
        "source": "jamal",
        "jamal": card,
        "smart_tags": [{"key": "jamal", "ar": "🎯 استراتيجية جمال"}],
        "smart_notes": card.get("checks_ar") or [],
        "expired": state in (JamalState.EXPIRED.value, JamalState.STOPPED.value, JamalState.NO_CHASE.value),
        "flow_ok": True,
    }


# ---- Pure helpers for unit tests (no market I/O) ----

def calc_levels(
    *,
    entry: float,
    stop: float,
    tp1_rr: float = 1.5,
    tp2_rr: float = 2.5,
) -> dict[str, float]:
    risk = max(float(entry) - float(stop), 0.0)
    return {
        "risk": round(risk, 4),
        "tp1": round(float(entry) + risk * float(tp1_rr), 4),
        "tp2": round(float(entry) + risk * float(tp2_rr), 4),
    }


def classify_chase(*, last: float, entry: float, max_chase_pct: float = 1.0) -> bool:
    if entry <= 0:
        return False
    return float(last) > float(entry) * (1.0 + float(max_chase_pct) / 100.0)

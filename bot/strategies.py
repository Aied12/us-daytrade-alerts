from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from bot.market_data import QuoteSnapshot

NY = ZoneInfo("America/New_York")


@dataclass
class StrategyHit:
    name: str
    name_ar: str
    points: float  # contribution toward 0-100
    side: str  # long / avoid / neutral  (no short — US cash equities long-only)
    note: str


def _vol_ratio(s: QuoteSnapshot) -> float:
    return s.volume / s.avg_volume_20 if s.avg_volume_20 > 0 else 1.0


def _vwap_dist(s: QuoteSnapshot) -> float:
    if not s.vwap_proxy:
        return 0.0
    return ((s.last - s.vwap_proxy) / s.vwap_proxy) * 100


def _now_ny():
    return datetime.now(NY).time()


def strat_gap(s: QuoteSnapshot) -> StrategyHit | None:
    # فجوة صاعدة مع ثبات = استمرار شراء
    if s.gap_pct >= 1.2 and s.last >= s.open and s.change_pct > 0:
        return StrategyHit("gap_up", "افتتاح فجوة صاعدة", 12, "long", f"فجوة +{s.gap_pct:.1f}% مع ثبات فوق الافتتاح")
    if s.gap_pct <= -1.5 and s.last > s.open and abs(s.gap_pct) >= 1.5:
        return StrategyHit("gap_fill", "ارتداد لملء فجوة هابطة", 8, "long", f"فجوة {s.gap_pct:.1f}% مع ارتداد")
    if s.gap_pct >= 3 and s.last < s.open:
        return StrategyHit("gap_fade", "فشل فجوة صاعدة", 10, "avoid", "فجوة كبيرة وتراجع تحت الافتتاح — لا تلاحق")
    return None


def strat_vwap(s: QuoteSnapshot) -> StrategyHit | None:
    dist = _vwap_dist(s)
    if -0.6 <= dist <= 0.4 and s.change_pct >= 0 and s.last >= s.vwap_proxy:
        return StrategyHit("vwap_bounce", "ارتداد VWAP", 11, "long", f"قرب/فوق VWAP ({dist:+.2f}%)")
    if dist < -1.2 and s.change_pct < 0:
        return StrategyHit("vwap_reject", "تحت VWAP بضعف", 6, "avoid", f"بعيد تحت VWAP ({dist:+.2f}%) — لا شراء")
    return None


def strat_ma_cross(s: QuoteSnapshot) -> StrategyHit | None:
    if s.ma20_prev <= s.ma50_prev and s.ma20 > s.ma50:
        return StrategyHit("ma_golden", "تقاطع متوسطات صاعد", 14, "long", "MA20 قطع MA50 للأعلى")
    if s.ma20_prev >= s.ma50_prev and s.ma20 < s.ma50:
        return StrategyHit("ma_death", "تقاطع متوسطات هابط", 10, "avoid", "MA20 قطع MA50 للأسفل — تجنّب الشراء")
    if s.last > s.ma20 > s.ma50 and s.change_pct > 0:
        return StrategyHit("ma_stack", "ترتيب متوسطات صاعد", 6, "long", "السعر فوق MA20 فوق MA50")
    return None


def strat_volume_spike(s: QuoteSnapshot) -> StrategyHit | None:
    vr = _vol_ratio(s)
    if vr >= 2.0 and s.change_pct >= 0.8:
        return StrategyHit("vol_spike_up", "حجم غير طبيعي صاعد", 13, "long", f"حجم ×{vr:.1f} مع صعود")
    if vr >= 2.0 and s.change_pct <= -0.8:
        return StrategyHit("vol_spike_down", "حجم غير طبيعي هابط", 10, "avoid", f"حجم ×{vr:.1f} مع هبوط — لا شراء")
    if vr >= 1.5 and s.change_pct >= 0:
        return StrategyHit("vol_elevated", "حجم مرتفع", 5, "neutral", f"حجم ×{vr:.1f}")
    return None


def strat_breakout_20(s: QuoteSnapshot) -> StrategyHit | None:
    if s.last >= s.high_20 * 0.998 and s.change_pct > 0:
        return StrategyHit("breakout_20", "كسر قمة 20 يوم", 15, "long", f"قرب/فوق قمة 20ي ${s.high_20:.2f}")
    if s.last <= s.low_20 * 1.002 and s.change_pct < 0:
        return StrategyHit("breakdown_20", "كسر قاع 20 يوم", 10, "avoid", f"قرب/تحت قاع 20ي ${s.low_20:.2f} — ضعف")
    return None


def strat_earnings(s: QuoteSnapshot) -> StrategyHit | None:
    if s.days_to_earnings is None:
        return None
    d = s.days_to_earnings
    if 0 <= d <= 2:
        return StrategyHit("earnings_soon", "قرب تقرير أرباح", 10, "avoid", f"أرباح خلال {d} يوم — تقلّب عالي")
    if 3 <= d <= 7 and s.change_pct > 1 and _vol_ratio(s) >= 1.2:
        return StrategyHit("pre_earnings_momentum", "زخم قبل الأرباح", 7, "long", f"أرباح بعد {d} يوم مع زخم")
    return None


def strat_sector(s: QuoteSnapshot, sector_mom: dict[str, float]) -> StrategyHit | None:
    mom = sector_mom.get(s.sector)
    if mom is None:
        return None
    if mom >= 1.0 and s.change_pct >= 0.5:
        return StrategyHit("sector_hot", f"زخم قطاع {s.sector}", 9, "long", f"قطاع {s.sector} {mom:+.1f}%")
    if mom <= -1.0 and s.change_pct <= -0.5:
        return StrategyHit("sector_weak", f"ضعف قطاع {s.sector}", 8, "avoid", f"قطاع {s.sector} {mom:+.1f}% — تجنّب")
    return None


def strat_corr(s: QuoteSnapshot, spy_chg: float, qqq_chg: float) -> StrategyHit | None:
    if s.symbol in ("SPY", "QQQ", "IWM"):
        return None
    if s.corr_qqq >= 0.7 and qqq_chg >= 0.5 and s.change_pct >= 0.4:
        return StrategyHit("beta_qqq", "ارتباط قوي مع QQQ", 7, "long", f"corr QQQ={s.corr_qqq:.2f}")
    if s.corr_spy >= 0.7 and spy_chg <= -0.8 and s.change_pct > 0.5:
        return StrategyHit("diverg_spy", "تفوق على SPY", 8, "long", "السهم صاعد والمؤشر ضعيف نسبيًا")
    if s.corr_spy >= 0.75 and spy_chg <= -1.0 and s.change_pct <= -0.5:
        return StrategyHit("drag_spy", "ضعف مع SPY", 6, "avoid", f"corr SPY={s.corr_spy:.2f} — لا شراء")
    return None


def strat_rsi(s: QuoteSnapshot) -> StrategyHit | None:
    if s.rsi_14 >= 78:
        return StrategyHit("rsi_ob", "RSI تشبع شرائي", 9, "avoid", f"RSI {s.rsi_14:.0f} — لا تطارد")
    if s.rsi_14 <= 25 and s.change_pct >= 0:
        return StrategyHit("rsi_os_bounce", "RSI تشبع بيعي + ارتداد", 10, "long", f"RSI {s.rsi_14:.0f}")
    if 45 <= s.rsi_14 <= 65 and s.change_pct > 0:
        return StrategyHit("rsi_ok", "RSI متوازن", 4, "neutral", f"RSI {s.rsi_14:.0f}")
    return None


def strat_candle(s: QuoteSnapshot) -> StrategyHit | None:
    p = s.candle_pattern
    if not p:
        return None
    if p in ("مطرقة", "ابتلاع صاعد") and s.change_pct >= -0.3:
        return StrategyHit("candle", f"شمعة: {p}", 9, "long", p)
    if p == "دوجي" and s.change_pct >= -0.3:
        return StrategyHit("candle", f"شمعة: {p}", 3, "neutral", p)
    if p in ("شهاب", "ابتلاع هابط"):
        return StrategyHit("candle", f"شمعة: {p}", 9, "avoid", f"{p} — إشارة ضعف، لا شراء")
    return None


def strat_sr(s: QuoteSnapshot) -> StrategyHit | None:
    if s.resistance and s.last >= s.resistance * 0.995 and s.change_pct > 0:
        return StrategyHit("res_break", "اختبار/كسر مقاومة", 10, "long", f"مقاومة≈${s.resistance:.2f}")
    if s.support and s.last <= s.support * 1.008 and s.change_pct >= -0.5 and s.last >= s.support:
        return StrategyHit("sup_hold", "ارتداد من دعم", 10, "long", f"دعم≈${s.support:.2f}")
    if s.support and s.last < s.support * 0.995:
        return StrategyHit("sup_break", "كسر دعم", 9, "avoid", f"دعم≈${s.support:.2f} — ضعف")
    return None


def strat_volatility(s: QuoteSnapshot, prefer: str) -> StrategyHit | None:
    if prefer == "high" and s.atr_pct >= 2.5 and s.change_pct > 0:
        return StrategyHit("high_vol", "تقلّب عالي", 5, "neutral", f"ATR% {s.atr_pct:.1f}")
    if prefer == "low" and s.atr_pct <= 1.4:
        return StrategyHit("low_vol", "تقلّب منخفض (محافظ)", 5, "neutral", f"ATR% {s.atr_pct:.1f}")
    if prefer == "high" and s.atr_pct < 1.2:
        return StrategyHit("too_quiet", "تقلّب ضعيف للمضاربة", 4, "avoid", f"ATR% {s.atr_pct:.1f}")
    return None


def strat_open15(s: QuoteSnapshot) -> StrategyHit | None:
    now = _now_ny()
    t0930 = datetime.strptime("09:30", "%H:%M").time()
    t1000 = datetime.strptime("10:00", "%H:%M").time()
    if not (t0930 <= now <= t1000):
        if abs(s.gap_pct) >= 1 and s.open_range_proxy_pct >= 1.2 and s.change_pct > 0 and s.gap_pct > 0:
            return StrategyHit("open_drive", "زخم اتجاه الافتتاح", 6, "long", "اتجاه الفجوة مستمر صاعد")
        return None
    if s.gap_pct >= 0.8 and s.last >= s.open:
        return StrategyHit("open15_long", "أول 15د — زخم صاعد", 12, "long", "نافذة الافتتاح")
    if s.gap_pct <= -0.8 and s.last <= s.open:
        return StrategyHit("open15_weak", "أول 15د — ضعف", 8, "avoid", "افتتاح هابط — لا شراء مبكر")
    return None


def strat_power_hour(s: QuoteSnapshot) -> StrategyHit | None:
    now = _now_ny()
    if not (datetime.strptime("15:00", "%H:%M").time() <= now <= datetime.strptime("16:00", "%H:%M").time()):
        return None
    if s.change_pct >= 1.0 and s.last >= s.day_high * 0.99:
        return StrategyHit("power_long", "آخر ساعة — استمرار صاعد", 11, "long", "Power Hour")
    if s.change_pct <= -1.0 and s.last <= s.day_low * 1.01:
        return StrategyHit("power_weak", "آخر ساعة — ضعف", 8, "avoid", "Power Hour هابط — لا شراء")
    return None


def strat_news(s: QuoteSnapshot) -> StrategyHit | None:
    if s.news_negative:
        return StrategyHit("neg_news", "أخبار سلبية قوية", 15, "avoid", "عنوان سلبي حديث — تجنّب")
    return None


# ── استراتيجيات جديدة (Long فقط) ─────────────────────────────────


def strat_orb(s: QuoteSnapshot) -> StrategyHit | None:
    """كسر نطاق الافتتاح للأعلى مع حجم."""
    if s.open <= 0 or s.day_high <= s.open:
        return None
    or_high = max(s.open * (1 + max(s.open_range_proxy_pct, 0.6) / 200), s.open * 1.004)
    vr = _vol_ratio(s)
    if s.last >= or_high and s.last >= s.open and s.change_pct >= 0.5 and vr >= 1.15:
        return StrategyHit(
            "orb_long",
            "كسر نطاق الافتتاح",
            12,
            "long",
            f"فوق افتتاح/نطاق مبكر مع حجم ×{vr:.1f}",
        )
    return None


def strat_vwap_reclaim(s: QuoteSnapshot) -> StrategyHit | None:
    """استعادة VWAP بعد ضعف — أقوى من مجرد الارتداد."""
    if not s.vwap_proxy:
        return None
    dist = _vwap_dist(s)
    vr = _vol_ratio(s)
    # كان اليوم متذبذب/ضعيف ثم السعر فوق VWAP بقليل مع صعود وحجم
    if 0 <= dist <= 0.9 and s.change_pct >= 0.35 and s.last > s.open and vr >= 1.2:
        if s.low < s.vwap_proxy * 0.997 or s.range_pct >= 1.0:
            return StrategyHit(
                "vwap_reclaim",
                "استعادة VWAP",
                13,
                "long",
                f"عودة فوق VWAP ({dist:+.2f}%) مع حجم ×{vr:.1f}",
            )
    return None


def strat_ma20_pullback(s: QuoteSnapshot) -> StrategyHit | None:
    """اتجاه صاعد + لمس/قرب MA20 ثم ارتداد."""
    if s.ma20 <= 0 or not (s.last > s.ma50 > 0):
        return None
    dist_ma = ((s.last - s.ma20) / s.ma20) * 100
    if s.ma20 > s.ma50 and -0.8 <= dist_ma <= 0.6 and s.change_pct >= 0.2 and s.last >= s.ma20 * 0.998:
        return StrategyHit(
            "ma20_pullback",
            "ارتداد من MA20",
            11,
            "long",
            f"قرب MA20 ({dist_ma:+.2f}%) ضمن اتجاه صاعد",
        )
    return None


def strat_compression_break(s: QuoteSnapshot) -> StrategyHit | None:
    """ضغط مدى ثم انفجار صاعد مع حجم."""
    vr = _vol_ratio(s)
    # مدى يومي بدأ ضيقاً نسبياً ثم حركة صاعدة واضحة
    if s.atr_pct >= 1.5 and 0.5 <= s.range_pct <= 2.2 and s.change_pct >= 0.9 and vr >= 1.4:
        if s.last >= (s.open + s.day_high) / 2:
            return StrategyHit(
                "compression_break",
                "ضغط ثم انفجار",
                12,
                "long",
                f"انفجار صاعد +{s.change_pct:.1f}% بعد مدى مضغوط · حجم ×{vr:.1f}",
            )
    return None


def strat_rel_strength(s: QuoteSnapshot, spy_chg: float, qqq_chg: float) -> StrategyHit | None:
    """تفوق نسبي واضح على السوق — شراء فقط."""
    if s.symbol in ("SPY", "QQQ", "IWM"):
        return None
    bench = max(spy_chg, qqq_chg)
    edge = s.change_pct - bench
    if s.change_pct >= 0.6 and edge >= 0.8:
        return StrategyHit(
            "rel_strength",
            "تفوق على السوق",
            10,
            "long",
            f"أقوى من المؤشر بـ {edge:+.1f}% (السهم {s.change_pct:+.1f}% / السوق {bench:+.1f}%)",
        )
    if s.change_pct <= -0.3 and edge <= -1.0:
        return StrategyHit(
            "rel_weakness",
            "ضعف أمام السوق",
            8,
            "avoid",
            f"أضعف من المؤشر بـ {edge:.1f}% — لا شراء",
        )
    return None


def strat_day_high_hold(s: QuoteSnapshot) -> StrategyHit | None:
    """ثبات قرب قمة اليوم مع زخم وحجم."""
    if s.day_high <= 0:
        return None
    near = s.last >= s.day_high * 0.992
    vr = _vol_ratio(s)
    if near and s.change_pct >= 0.7 and vr >= 1.15 and s.last >= s.open:
        return StrategyHit(
            "day_high_hold",
            "قرب قمة اليوم مع زخم",
            11,
            "long",
            f"يثبت قرب أعلى اليوم ${s.day_high:.2f} · حجم ×{vr:.1f}",
        )
    return None


def strat_failed_breakdown(s: QuoteSnapshot) -> StrategyHit | None:
    """فشل كسر هبوطي: لمس تحت الدعم/الافتتاح ثم استعادة سريعة."""
    if not s.support and s.open <= 0:
        return None
    floor = s.support if s.support > 0 else s.open
    # القاع اليومي اخترق قليلاً ثم الإغلاق/السعر فوق المستوى مع صعود
    if s.day_low < floor * 0.995 and s.last > floor * 1.002 and s.change_pct >= 0.25:
        return StrategyHit(
            "failed_breakdown",
            "فشل كسر هبوطي",
            12,
            "long",
            f"كسر وهمي ثم استعادة فوق ${floor:.2f}",
        )
    return None


def strat_midday_continuation(s: QuoteSnapshot) -> StrategyHit | None:
    """استمرار صاعد بعد هدوء منتصف الجلسة."""
    now = _now_ny()
    if not (datetime.strptime("11:30", "%H:%M").time() <= now <= datetime.strptime("14:00", "%H:%M").time()):
        return None
    vr = _vol_ratio(s)
    if s.change_pct >= 1.0 and s.last > s.open and s.last >= s.vwap_proxy and vr >= 1.1:
        return StrategyHit(
            "midday_cont",
            "زخم منتصف الجلسة",
            9,
            "long",
            f"استمرار +{s.change_pct:.1f}% فوق VWAP بعد الظهر",
        )
    return None


def strat_chase_warning(s: QuoteSnapshot) -> StrategyHit | None:
    """تحذير مطاردة بعد امتداد قوي."""
    if s.change_pct >= 4.0 and s.rsi_14 >= 72:
        return StrategyHit(
            "chase_warn",
            "تحذير مطاردة مفرطة",
            12,
            "avoid",
            f"امتداد +{s.change_pct:.1f}% و RSI {s.rsi_14:.0f} — لا تلحق",
        )
    dist = _vwap_dist(s)
    if s.change_pct >= 3.0 and dist >= 2.8:
        return StrategyHit(
            "chase_vwap",
            "ممتد بعيداً فوق VWAP",
            10,
            "avoid",
            f"فوق VWAP بـ {dist:.1f}% — مخاطرة مطاردة",
        )
    return None


def strat_dollar_surge(s: QuoteSnapshot) -> StrategyHit | None:
    """اندفاع سيولة دولارية مع اتجاه صاعد."""
    dollar = float(s.last) * float(s.volume or 0)
    avg_dollar = float(s.last) * float(s.avg_volume_20 or 0)
    if avg_dollar <= 0:
        return None
    mult = dollar / avg_dollar
    if mult >= 1.8 and s.change_pct >= 0.7 and dollar >= 8_000_000:
        return StrategyHit(
            "dollar_surge",
            "اندفاع سيولة دولارية",
            12,
            "long",
            f"تداول ≈${dollar/1e6:.0f}M (×{mult:.1f} من المتوسط) مع صعود",
        )
    return None


def strat_gap_and_go(s: QuoteSnapshot) -> StrategyHit | None:
    """فجوة صاعدة + تثبيت قوي فوق الافتتاح (Gap & Go)."""
    vr = _vol_ratio(s)
    if (
        s.gap_pct >= 1.0
        and s.last >= s.open * 1.002
        and s.change_pct >= s.gap_pct * 0.5
        and s.last >= s.vwap_proxy
        and vr >= 1.2
    ):
        return StrategyHit(
            "gap_and_go",
            "فجوة واستمرار (Gap & Go)",
            14,
            "long",
            f"فجوة +{s.gap_pct:.1f}% وتثبيت صاعد مع حجم ×{vr:.1f}",
        )
    return None


def evaluate_all(
    s: QuoteSnapshot,
    *,
    sector_mom: dict[str, float],
    spy_chg: float,
    qqq_chg: float,
    vol_preference: str = "high",
) -> list[StrategyHit]:
    hits: list[StrategyHit | None] = [
        # أصلية (Long / Avoid / Neutral فقط)
        strat_gap(s),
        strat_vwap(s),
        strat_ma_cross(s),
        strat_volume_spike(s),
        strat_breakout_20(s),
        strat_earnings(s),
        strat_sector(s, sector_mom),
        strat_corr(s, spy_chg, qqq_chg),
        strat_rsi(s),
        strat_candle(s),
        strat_sr(s),
        strat_volatility(s, vol_preference),
        strat_open15(s),
        strat_power_hour(s),
        strat_news(s),
        # جديدة
        strat_orb(s),
        strat_vwap_reclaim(s),
        strat_ma20_pullback(s),
        strat_compression_break(s),
        strat_rel_strength(s, spy_chg, qqq_chg),
        strat_day_high_hold(s),
        strat_failed_breakdown(s),
        strat_midday_continuation(s),
        strat_chase_warning(s),
        strat_dollar_surge(s),
        strat_gap_and_go(s),
    ]
    # حزام أمان: لا نمرّر أي side=short أبداً
    out: list[StrategyHit] = []
    for h in hits:
        if not h:
            continue
        if h.side == "short":
            out.append(StrategyHit(h.name, h.name_ar, h.points, "avoid", h.note + " — Long فقط"))
        else:
            out.append(h)
    return out

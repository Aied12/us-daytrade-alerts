from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from bot.market_data import QuoteSnapshot

NY = ZoneInfo("America/New_York")


@dataclass
class StrategyHit:
    name: str
    name_ar: str
    points: float  # contribution toward 0-100
    side: str  # long / short / avoid / neutral
    note: str


def _vol_ratio(s: QuoteSnapshot) -> float:
    return s.volume / s.avg_volume_20 if s.avg_volume_20 > 0 else 1.0


def strat_gap(s: QuoteSnapshot) -> StrategyHit | None:
    # 16 Gap: gap up with hold above open = continuation; gap down fill watch
    if s.gap_pct >= 1.2 and s.last >= s.open and s.change_pct > 0:
        return StrategyHit("gap_up", "افتتاح فجوة صاعدة", 12, "long", f"فجوة +{s.gap_pct:.1f}% مع ثبات فوق الافتتاح")
    if s.gap_pct <= -1.5 and s.last > s.open and abs(s.gap_pct) >= 1.5:
        return StrategyHit("gap_fill", "ارتداد لملء فجوة هابطة", 8, "long", f"فجوة {s.gap_pct:.1f}% مع ارتداد")
    if s.gap_pct >= 3 and s.last < s.open:
        return StrategyHit("gap_fade", "فشل فجوة صاعدة", 10, "avoid", "فجوة كبيرة وتراجع تحت الافتتاح — لا تلاحق")
    return None


def strat_vwap(s: QuoteSnapshot) -> StrategyHit | None:
    # 17 VWAP bounce proxy
    dist = ((s.last - s.vwap_proxy) / s.vwap_proxy) * 100 if s.vwap_proxy else 0
    if -0.6 <= dist <= 0.4 and s.change_pct >= 0 and s.last >= s.vwap_proxy:
        return StrategyHit("vwap_bounce", "ارتداد VWAP", 11, "long", f"قرب/فوق VWAP ({dist:+.2f}%)")
    if dist < -1.2 and s.change_pct < 0:
        return StrategyHit("vwap_reject", "تحت VWAP بضعف", 6, "avoid", f"بعيد تحت VWAP ({dist:+.2f}%)")
    return None


def strat_ma_cross(s: QuoteSnapshot) -> StrategyHit | None:
    # 18 MA crossover
    if s.ma20_prev <= s.ma50_prev and s.ma20 > s.ma50:
        return StrategyHit("ma_golden", "تقاطع متوسطات صاعد", 14, "long", "MA20 قطع MA50 للأعلى")
    if s.ma20_prev >= s.ma50_prev and s.ma20 < s.ma50:
        return StrategyHit("ma_death", "تقاطع متوسطات هابط", 12, "short", "MA20 قطع MA50 للأسفل")
    if s.last > s.ma20 > s.ma50 and s.change_pct > 0:
        return StrategyHit("ma_stack", "ترتيب متوسطات صاعد", 6, "long", "السعر فوق MA20 فوق MA50")
    return None


def strat_volume_spike(s: QuoteSnapshot) -> StrategyHit | None:
    # 19
    vr = _vol_ratio(s)
    if vr >= 2.0 and s.change_pct >= 0.8:
        return StrategyHit("vol_spike_up", "حجم غير طبيعي صاعد", 13, "long", f"حجم ×{vr:.1f} مع صعود")
    if vr >= 2.0 and s.change_pct <= -0.8:
        return StrategyHit("vol_spike_down", "حجم غير طبيعي هابط", 12, "short", f"حجم ×{vr:.1f} مع هبوط")
    if vr >= 1.5:
        return StrategyHit("vol_elevated", "حجم مرتفع", 5, "neutral", f"حجم ×{vr:.1f}")
    return None


def strat_breakout_20(s: QuoteSnapshot) -> StrategyHit | None:
    # 20
    if s.last >= s.high_20 * 0.998 and s.change_pct > 0:
        return StrategyHit("breakout_20", "كسر قمة 20 يوم", 15, "long", f"قرب/فوق قمة 20ي ${s.high_20:.2f}")
    if s.last <= s.low_20 * 1.002 and s.change_pct < 0:
        return StrategyHit("breakdown_20", "كسر قاع 20 يوم", 12, "short", f"قرب/تحت قاع 20ي ${s.low_20:.2f}")
    return None


def strat_earnings(s: QuoteSnapshot) -> StrategyHit | None:
    # 21
    if s.days_to_earnings is None:
        return None
    d = s.days_to_earnings
    if 0 <= d <= 2:
        return StrategyHit("earnings_soon", "قرب تقرير أرباح", 10, "avoid", f"أرباح خلال {d} يوم — تقلّب عالي")
    if 3 <= d <= 7 and s.change_pct > 1 and _vol_ratio(s) >= 1.2:
        return StrategyHit("pre_earnings_momentum", "زخم قبل الأرباح", 7, "long", f"أرباح بعد {d} يوم مع زخم")
    return None


def strat_sector(s: QuoteSnapshot, sector_mom: dict[str, float]) -> StrategyHit | None:
    # 22
    mom = sector_mom.get(s.sector)
    if mom is None:
        return None
    if mom >= 1.0 and s.change_pct >= 0.5:
        return StrategyHit("sector_hot", f"زخم قطاع {s.sector}", 9, "long", f"قطاع {s.sector} {mom:+.1f}%")
    if mom <= -1.0 and s.change_pct <= -0.5:
        return StrategyHit("sector_weak", f"ضعف قطاع {s.sector}", 8, "short", f"قطاع {s.sector} {mom:+.1f}%")
    return None


def strat_corr(s: QuoteSnapshot, spy_chg: float, qqq_chg: float) -> StrategyHit | None:
    # 23
    if s.symbol in ("SPY", "QQQ", "IWM"):
        return None
    if s.corr_qqq >= 0.7 and qqq_chg >= 0.5 and s.change_pct >= 0.4:
        return StrategyHit("beta_qqq", "ارتباط قوي مع QQQ", 7, "long", f"corr QQQ={s.corr_qqq:.2f}")
    if s.corr_spy >= 0.7 and spy_chg <= -0.8 and s.change_pct > 0.5:
        return StrategyHit("diverg_spy", "تفوق على SPY", 8, "long", "السهم صاعد والمؤشر ضعيف نسبيًا")
    if s.corr_spy >= 0.75 and spy_chg <= -1.0 and s.change_pct <= -0.5:
        return StrategyHit("drag_spy", "سحب مع SPY", 6, "short", f"corr SPY={s.corr_spy:.2f}")
    return None


def strat_short(s: QuoteSnapshot) -> StrategyHit | None:
    # 24 cautious short
    if (
        s.change_pct <= -1.2
        and s.last < s.ma20
        and _vol_ratio(s) >= 1.3
        and s.rsi_14 <= 45
    ):
        return StrategyHit("short_setup", "إعداد بيع قصير بحذر", 11, "short", "ضعف + تحت MA20 + حجم")
    return None


def strat_rsi(s: QuoteSnapshot) -> StrategyHit | None:
    # 25
    if s.rsi_14 >= 78:
        return StrategyHit("rsi_ob", "RSI تشبع شرائي", 9, "avoid", f"RSI {s.rsi_14:.0f}")
    if s.rsi_14 <= 25 and s.change_pct >= 0:
        return StrategyHit("rsi_os_bounce", "RSI تشبع بيعي + ارتداد", 10, "long", f"RSI {s.rsi_14:.0f}")
    if 45 <= s.rsi_14 <= 65:
        return StrategyHit("rsi_ok", "RSI متوازن", 4, "neutral", f"RSI {s.rsi_14:.0f}")
    return None


def strat_candle(s: QuoteSnapshot) -> StrategyHit | None:
    # 26
    p = s.candle_pattern
    if not p:
        return None
    if p in ("مطرقة", "ابتلاع صاعد", "دوجي") and s.change_pct >= -0.3:
        side = "long" if p != "دوجي" else "neutral"
        pts = 9 if p != "دوجي" else 3
        return StrategyHit("candle", f"شمعة: {p}", pts, side, p)
    if p in ("شهاب", "ابتلاع هابط"):
        return StrategyHit("candle", f"شمعة: {p}", 9, "short" if p == "ابتلاع هابط" else "avoid", p)
    return None


def strat_sr(s: QuoteSnapshot) -> StrategyHit | None:
    # 27
    if s.resistance and s.last >= s.resistance * 0.995 and s.change_pct > 0:
        return StrategyHit("res_break", "اختبار/كسر مقاومة", 10, "long", f"مقاومة≈${s.resistance:.2f}")
    if s.support and s.last <= s.support * 1.008 and s.change_pct >= -0.5 and s.last >= s.support:
        return StrategyHit("sup_hold", "ارتداد من دعم", 10, "long", f"دعم≈${s.support:.2f}")
    if s.support and s.last < s.support * 0.995:
        return StrategyHit("sup_break", "كسر دعم", 9, "short", f"دعم≈${s.support:.2f}")
    return None


def strat_volatility(s: QuoteSnapshot, prefer: str) -> StrategyHit | None:
    # 28 high vol / 29 low vol
    if prefer == "high" and s.atr_pct >= 2.5:
        return StrategyHit("high_vol", "تقلّب عالي", 5, "neutral", f"ATR% {s.atr_pct:.1f}")
    if prefer == "low" and s.atr_pct <= 1.4:
        return StrategyHit("low_vol", "تقلّب منخفض (محافظ)", 5, "neutral", f"ATR% {s.atr_pct:.1f}")
    if prefer == "high" and s.atr_pct < 1.2:
        return StrategyHit("too_quiet", "تقلّب ضعيف للمضاربة", 4, "avoid", f"ATR% {s.atr_pct:.1f}")
    return None


def strat_open15(s: QuoteSnapshot) -> StrategyHit | None:
    # 30 first 15 min proxy using NY clock + gap/range
    now = datetime.now(NY).time()
    if not (datetime.strptime("09:30", "%H:%M").time() <= now <= datetime.strptime("10:00", "%H:%M").time()):
        # still score structural open-drive outside window lightly
        if abs(s.gap_pct) >= 1 and s.open_range_proxy_pct >= 1.2 and s.change_pct * s.gap_pct > 0:
            return StrategyHit("open_drive", "زخم اتجاه الافتتاح", 6, "long" if s.change_pct > 0 else "short", "اتجاه الفجوة مستمر")
        return None
    if s.gap_pct >= 0.8 and s.last >= s.open:
        return StrategyHit("open15_long", "أول 15د — زخم صاعد", 12, "long", "نافذة الافتتاح")
    if s.gap_pct <= -0.8 and s.last <= s.open:
        return StrategyHit("open15_short", "أول 15د — زخم هابط", 10, "short", "نافذة الافتتاح")
    return None


def strat_power_hour(s: QuoteSnapshot) -> StrategyHit | None:
    # 31 last hour
    now = datetime.now(NY).time()
    if not (datetime.strptime("15:00", "%H:%M").time() <= now <= datetime.strptime("16:00", "%H:%M").time()):
        return None
    if s.change_pct >= 1.0 and s.last >= s.day_high * 0.99:
        return StrategyHit("power_long", "آخر ساعة — استمرار صاعد", 11, "long", "Power Hour")
    if s.change_pct <= -1.0 and s.last <= s.day_low * 1.01:
        return StrategyHit("power_short", "آخر ساعة — استمرار هابط", 10, "short", "Power Hour")
    return None


def strat_news(s: QuoteSnapshot) -> StrategyHit | None:
    # 32
    if s.news_negative:
        return StrategyHit("neg_news", "أخبار سلبية قوية", 15, "avoid", "عنوان سلبي حديث — تجنّب")
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
        strat_gap(s),
        strat_vwap(s),
        strat_ma_cross(s),
        strat_volume_spike(s),
        strat_breakout_20(s),
        strat_earnings(s),
        strat_sector(s, sector_mom),
        strat_corr(s, spy_chg, qqq_chg),
        strat_short(s),
        strat_rsi(s),
        strat_candle(s),
        strat_sr(s),
        strat_volatility(s, vol_preference),
        strat_open15(s),
        strat_power_hour(s),
        strat_news(s),
    ]
    return [h for h in hits if h]

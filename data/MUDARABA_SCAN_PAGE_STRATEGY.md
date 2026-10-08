# US Pre-Market ORB Retest — Scan Page Pack

## Watchlist (الأسهم المعتمدة فقط)

| # | Symbol | Type | Notes |
|---|--------|------|-------|
| 1 | SPY | ETF | مؤشر S&P — سيولة عالية |
| 2 | AAPL | Mega-cap | أفضل أداء في الباك تست |
| 3 | TSLA | Mega-cap | تقلب عالي — مناسب للسكالب |
| 4 | META | Mega-cap | استقرار جيد في العينة |

**Excluded (لا تُضاف للسكان حالياً):** NVDA, AMD, QQQ

---

## Scanner filters (صفحة السكان)

```
MARKET: US Equities
WATCHLIST: SPY, AAPL, TSLA, META
TIMEZONE: America/New_York (ET)
CHART TIMEFRAME: 5 minutes
EXTENDED HOURS: ON (Pre/Post market visible)
SESSION FILTER: Regular + Pre-Market
```

### Premarket range (يُحسب يومياً)
```
PREMARKET_START: 04:00 ET
PREMARKET_END:   09:29 ET
PMH = highest high of premarket bars
PML = lowest low of premarket bars
PM_MID = (PMH + PML) / 2
PM_RANGE_PCT = (PMH - PML) / PM_MID
```

### Eligibility (قبل أي تنبيه دخول)
```
MIN_PM_RANGE_PCT: 0.0015          # 0.15%
SKIP_IF_BOTH_SIDES_BREAK: true
MAX_TRADES_PER_SYMBOL_PER_DAY: 1
NO_TRADE_NEAR_MAJOR_NEWS: ±30 min  # CPI / FOMC / NFP / stock earnings
```

---

## Strategy settings (كاملة)

### 1) Identity
```
NAME: US_PM_ORB_RETEST_SCALP
STYLE: Day scalp / opening drive
DIRECTION: Long + Short
RISK_MODEL: Fixed fractional R
```

### 2) Time windows
```
PREMARKET_BUILD: 04:00 – 09:29 ET
TRADE_WINDOW:    09:35 – 11:30 ET
FORCE_FLAT:      11:30 ET
NO_NEW_ENTRIES_AFTER: 11:15 ET   # اختياري عملي
```

### 3) Levels
```
BREAKOUT_BUFFER_PCT: 0.0005      # 0.05%
LONG_BREAK_LEVEL:  PMH * (1 + 0.0005)
SHORT_BREAK_LEVEL: PML * (1 - 0.0005)
RETEST_TOLERANCE_PCT: 0.0015     # 0.15%
```

### 4) Signal logic (بالترتيب)

**A. Breakout**
```
LONG_BREAK  = Close > LONG_BREAK_LEVEL
SHORT_BREAK = Close < SHORT_BREAK_LEVEL
IF LONG_BREAK AND SHORT_BREAK same day → SKIP DAY
```

**B. Retest (بعد الاختراق فقط)**
```
LONG_RETEST  = after LONG_BREAK  AND Low  <= PMH * (1 + 0.0015)
SHORT_RETEST = after SHORT_BREAK AND High >= PML * (1 - 0.0015)
```

**C. Confirmation candle (5m)**
```
BULL_CONFIRM =
  (Bullish Engulfing)
  OR (Close > Open AND body/range >= 0.55 AND Close > prevClose)

BEAR_CONFIRM =
  (Bearish Engulfing)
  OR (Close < Open AND body/range >= 0.55 AND Close < prevClose)
```

**D. Entry**
```
LONG ENTRY:
  LONG_BREAK AND LONG_RETEST AND BULL_CONFIRM
  AND Close >= PMH * (1 - 0.0015)
  ENTRY = Close of confirmation candle

SHORT ENTRY:
  SHORT_BREAK AND SHORT_RETEST AND BEAR_CONFIRM
  AND Close <= PML * (1 + 0.0015)
  ENTRY = Close of confirmation candle
```

### 5) Risk / exits
```
R_MULTIPLE: 1.5
RISK_PER_TRADE: 0.5% – 1.0% of account   # أو مبلغ ثابت مثل $100

LONG STOP  = min(ConfirmLow, PMH) * 0.999
SHORT STOP = max(ConfirmHigh, PML) * 1.001

LONG TARGET  = ENTRY + 1.5 * (ENTRY - STOP)
SHORT TARGET = ENTRY - 1.5 * (STOP - ENTRY)

EXIT RULES:
1) Hit STOP → close
2) Hit TARGET → close
3) Time = 11:30 ET → market close remaining
4) If STOP and TARGET same bar → count as STOP (conservative)
```

### 6) Position sizing
```
RISK_USD = AccountEquity * RiskPercent
SHARES = floor( RISK_USD / abs(ENTRY - STOP) )
IF SHARES < 1 → skip trade
```

### 7) Alert / scan conditions (نص جاهز للسكان)

**Long alert**
```
In watchlist [SPY,AAPL,TSLA,META]
AND time between 09:35-11:30 America/New_York
AND PM_RANGE_PCT >= 0.15%
AND price broke above PMH by >= 0.05% earlier today
AND price did NOT break below PML today
AND current bar retested PMH (low within 0.15%)
AND current bar is bullish confirmation
→ ALERT: LONG_ORB_RETEST
```

**Short alert**
```
In watchlist [SPY,AAPL,TSLA,META]
AND time between 09:35-11:30 America/New_York
AND PM_RANGE_PCT >= 0.15%
AND price broke below PML by >= 0.05% earlier today
AND price did NOT break above PMH today
AND current bar retested PML (high within 0.15%)
AND current bar is bearish confirmation
→ ALERT: SHORT_ORB_RETEST
```

---

## Checklist سريع قبل الدخول

- [ ] السهم من القائمة الأربع فقط
- [ ] Pre-Market range ≥ 0.15%
- [ ] اختراق اتجاه واحد فقط (ليس الاثنين)
- [ ] الوقت بين 09:35 و 11:30 ET
- [ ] إعادة اختبار + شمعة تأكيد
- [ ] وقف وهدف 1.5R محسوبان قبل الدخول
- [ ] لا خبر كبير خلال ±30 دقيقة
- [ ] لم أدخل نفس السهم اليوم

---

## Backtest snapshot (مرجع)
```
Sample: ~60 trading days, 5m + extended hours
Trades: 155
Win rate: 54.8%
Avg R: +0.215
Profit factor: 1.64
Total R: +33.3
Max DD: -8.1R
```

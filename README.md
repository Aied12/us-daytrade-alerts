# مساعد تنبيهات التداول اليومي — أسهم أمريكية
# تنبيهات فقط — لا ينفّذ أوامر شراء/بيع

## ماذا يفعل؟
- تقرير صباحي: مزاج السوق + أفضل الفرص + ماذا تفعل
- تنبيهات أثناء الجلسة: دخول / راقب / تجنّب مع وقف وهدف وحجم الصفقة
- ملخص مسائي
- إدارة مخاطرة مربوطة برأس مالك (افتراضيًا 45,000 ر.س)
- دفتر صفقات يدوي لمراجعة أدائك

## رأس المال والمخاطرة (الافتراضي)
| البند | القيمة |
|------|--------|
| رأس المال | 45,000 ر.س ≈ $12,000 |
| مخاطرة الصفقة | 1% ≈ 450 ر.س |
| حد الخسارة اليومي | 2% ≈ 900 ر.س |
| أقصى تنبيهات دخول/يوم | 5 |

إذا وصلت حد الخسارة اليومي: **قف عن التداول**.

## التثبيت
```bash
cd us-daytrade-alerts
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## تفعيل تيليجرام (اختياري لكن مهم)
1. افتح @BotFather في تيليجرام وأنشئ بوتًا — انسخ التوكن
2. أرسل رسالة لبوتك، ثم افتح:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
   وانسخ `chat.id`
3. ضع القيم في `.env`:
```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
CAPITAL_SAR=45000
```

## التشغيل
```bash
# عرض إعدادات المخاطرة
python main.py risk

# تجربة كاملة (صباحي + تنبيهات + مسائي)
python main.py scan --mode demo

# حسب الوقت
python main.py scan --mode morning
python main.py scan --mode intraday
python main.py scan --mode evening

# تسجيل صفقة نفذتها يدويًا
python main.py journal-add --symbol AAPL --shares 10 --entry 190 --exit 192.5 --notes "breakout"

# ملخص الدفتر
python main.py journal
```

## جدولة يومية (مثال cron — بتوقيت Native مناسب لك)
```cron
# صباحي قبل الافتتاح الأمريكي (~13:30 الرياض شتاءً تقريباً — عدّل حسب التوقيت)
30 13 * * 1-5 cd /path/us-daytrade-alerts && .venv/bin/python main.py scan --mode morning
# فحص أثناء الجلسة كل 15 دقيقة
*/15 14-20 * * 1-5 cd /path/us-daytrade-alerts && .venv/bin/python main.py scan --mode intraday
# مسائي
15 21 * * 1-5 cd /path/us-daytrade-alerts && .venv/bin/python main.py scan --mode evening
```

## تنبيه قانوني
هذا أداة تعليمية للمساعدة في التنظيم والتنبيهات. ليست نصيحة استثمارية، ولا تضمن ربحًا، ولا تنفّذ أوامر نيابةً عنك.

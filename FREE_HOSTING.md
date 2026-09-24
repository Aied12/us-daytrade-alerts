# استضافة دائمة مجانية (بدون فلوس)

## وش راح يصير؟
1. **البوت** يشتغل من GitHub Actions مجانًا (تنبيهات تيليجرام)
2. **اللوحة** تتنشر على GitHub Pages برابط دائم يفتح من الجوال

شكل الرابط بعد التفعيل:
`https://USER.github.io/REPO/`

مثال:
`https://aied0101.github.io/us-daytrade-alerts/`

---

## الخطوات (مرة واحدة)

### 1) حساب GitHub مجاني
افتح: https://github.com/signup

### 2) مستودع جديد
- New repository
- الاسم مثل: `us-daytrade-alerts`
- Public
- Create

### 3) ارفع المشروع
من الكمبيوتر/أو من GitHub web ارفع مجلد المشروع كامل  
(أو اطلب مني لاحقًا أوامر الرفع إذا صار عندك مستودع)

### 4) Secrets للبوت
Repository → Settings → Secrets and variables → Actions → New repository secret:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TELEGRAM_CHANNEL_ID` = `@aied01`
- `CAPITAL_SAR` = `45000`

### 5) تفعيل Pages
Settings → Pages → Source: **GitHub Actions**

### 6) تشغيل أول مرة
Actions → us-daytrade-alerts → Run workflow → mode=`ping`

بعد دقائق يطلع رابط اللوحة من:
Settings → Pages → Your site is live at ...

---

## بديل فوري بدون صفحات
تيليجرام نفسه هو اللوحة الدائمة المجانية:
- البوت: https://t.me/Aied01_bot
- القناة: https://t.me/aied01
- الأمر: `/status`

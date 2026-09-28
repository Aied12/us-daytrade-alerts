# تحويل المشروع إلى VPS من الآيفون (خطوة بخطوة)

هذا الدليل للمبتدئ. بعد التنفيذ الصفحة تشتغل من السيرفر مباشرة وتتحدّث لحسابها — بدون تحديث يدوي كل شوي، وبدون GitHub Pages.

## ماذا تحتاج قبل ما تبدأ

1. حساب عند مزود VPS (الأنسب رخيصاً: **Hetzner** أو **Contabo** ≈ 4–6$/شهر)
2. تطبيق **Termius** من App Store (اتصال SSH من الآيفون)
3. نظام السيرفر: **Ubuntu 22.04 أو 24.04**
4. افتح المنفذ **80** في جدار النار عند المزود (Firewall / Security Group)

## الخطوة 1 — أنشئ السيرفر

- اختر أصغر خطة: 1 vCPU · 1–2GB RAM · 20GB SSD
- المنطقة: الأقرب لك (أو أوروبا)
- احفظ: **IP** + **root password** أو مفتاح SSH

## الخطوة 2 — ادخل من Termius

1. افتح Termius → New Host
2. Address = IP السيرفر
3. Username = `root`
4. Password = كلمة سر السيرفر
5. Connect

## الخطوة 3 — الصق أمر التثبيت مرة واحدة

انسخ هذا بالكامل والصقه في Termius ثم Enter:

```bash
curl -fsSL https://raw.githubusercontent.com/Aied12/us-daytrade-alerts/main/deploy/vps/setup.sh | bash
```

انتظر حتى تظهر رسالة:

`افتح من الآيفون: http://IP/`

## الخطوة 4 — افتح الصفحة

في Safari على الآيفون افتح:

`http://IP_السيرفر/`

مثال: `http://203.0.113.10/`

أضف للشاشة الرئيسية (Share → Add to Home Screen) عشان تصير مثل التطبيق.

## ماذا يعمل بعد التحويل؟

| الجزء | أين |
|--------|------|
| الفحص المستمر | VPS (كل ~45 ثانية) |
| الصفحة + JSON | VPS على المنفذ 80 |
| GitHub | للكود فقط (مو للنشر الحي) |
| تيليجرام | موقف افتراضياً — تفعّله من `.env` إذا حاب |

## أوامر مفيدة (Termius)

```bash
cd /opt/us-daytrade-alerts/deploy/vps
docker compose ps
docker compose logs -f bot
docker compose restart
```

تحديث الكود لاحقاً:

```bash
cd /opt/us-daytrade-alerts && git pull && cd deploy/vps && docker compose up -d --build
```

## تكبير السيرفر لاحقاً

من لوحة المزود: Resize / Upgrade → أعد تشغيل السيرفر → النظام يكمل كما هو.

## مشاكل شائعة

- الصفحة ما تفتح: تأكد المنفذ 80 مفتوح في Firewall.
- بيانات قديمة: انتظر دقيقة بعد التشغيل الأول (`docker compose logs -f bot`).
- نسيت الـ IP: في Termius ظاهر، أو `curl ifconfig.me` داخل السيرفر.

## بعد ما تتأكد أن VPS شغال

1. استخدم رابط الـ VPS يومياً من الآيفون
2. (اختياري) أوقف GitHub Actions `always-on` عشان ما يتعارض مع السيرفر
3. GitHub Pages تقدر تتركه كاحتياطي أو تعطّله لاحقاً

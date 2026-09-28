# التشغيل المستمر

## الأفضل الآن: VPS (موصى به)

دليل الآيفون خطوة بخطوة:

👉 [`deploy/VPS_IPHONE_AR.md`](./VPS_IPHONE_AR.md)

أمر تثبيت سريع على Ubuntu:

```bash
curl -fsSL https://raw.githubusercontent.com/Aied12/us-daytrade-alerts/main/deploy/vps/setup.sh | bash
```

بعدها افتح: `http://IP_السيرفر/`

---

## ما كان مفعّلاً سابقاً (GitHub Pages)

- GitHub Actions `always-on.yml` يحدّث اللوحة كل ~2 دقيقة
- الصفحة تقرأ من raw.githubusercontent / Pages
- مناسب كاحتياطي؛ للسلاسة اليومية استخدم VPS

## ترقية اختيارية: Cloudflare (بث أسرع للجوال)

```bash
cd cloudflare
npx wrangler login
npx wrangler kv namespace create LIVE_KV
# ضع الـ id في wrangler.toml
npx wrangler secret put INGEST_TOKEN
npx wrangler deploy
```

ثم في Secrets / `.env`:

- `LIVE_API_URL` = رابط الـ Worker
- `LIVE_API_TOKEN` = نفس INGEST_TOKEN

وفي المتصفح مرة واحدة:

```js
localStorage.setItem('dash_live_api', 'https://YOUR-WORKER.workers.dev')
```

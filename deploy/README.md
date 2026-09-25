# التشغيل المستمر — للمبتدئين

## ما تم تفعيله الآن (بدون تكلفة إضافية)
- GitHub Actions `always-on.yml` يحدّث اللوحة كل ~2 دقيقة أثناء الجلسة
- الصفحة تقرأ البيانات من raw.githubusercontent (مو من كاش Pages المعطوب)
- تيليجرام يبقى موقف في التحديث السحابي

## ترقية لاحقاً: Cloudflare (بث أسرع)
```bash
cd cloudflare
npx wrangler login
npx wrangler kv namespace create LIVE_KV
# ضع الـ id في wrangler.toml
npx wrangler secret put INGEST_TOKEN
npx wrangler deploy
```
ثم في GitHub Secrets:
- `LIVE_API_URL` = رابط الـ Worker
- `LIVE_API_TOKEN` = نفس INGEST_TOKEN

وفي المتصفح (مرة واحدة):
```js
localStorage.setItem('dash_live_api', 'https://YOUR-WORKER.workers.dev')
```

## ترقية لاحقاً: VPS
```bash
cd deploy/vps
docker compose up -d --build
```
أو:
```bash
python scripts/vps_loop.py
```

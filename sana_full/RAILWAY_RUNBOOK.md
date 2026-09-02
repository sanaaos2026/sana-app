# Railway Production Runbook

## الخدمة

- مشروع Sana الحالي أو مشروع جديد ضمن الحساب الحالي.
- خدمة Web واحدة، Replica واحدة، أقل موارد مناسبة.
- GitHub repo: `sanaaos2026/sana-app`.
- Branch: `main`.
- Root Directory: `sana_full`.
- Builder: Nixpacks.
- Healthcheck: `/healthz`.
- لا Database أو Redis أو Worker أو Volume.

## متغيرات المرحلة A

أضف القيم عبر Railway Variables فقط:

```text
DATABASE_URL=<existing Supabase pooler URL>
SESSION_SECRET=<production-only random value>
ADMIN_PREVIEW_KEY=<production-only value, if used>
APP_URL=<Railway temporary HTTPS URL>
SANA_ENV=production
ENABLE_EXECUTION_REMINDER_SCHEDULER=0
ENABLE_KNOWLEDGE_BACKUP_SCHEDULER=0
ENABLE_KNOWLEDGE_RESEARCH_SCHEDULER=0
```

لا تنسخ القيم إلى Git أو logs.
يجب أن يكون `SESSION_SECRET` عشوائيًا وثابتًا وطوله 32 محرفًا على الأقل؛
التطبيق يرفض الإقلاع بقيمة مفقودة أو قصيرة.

استخدم Supabase **Session Pooler** لا Transaction Pooler. يبدأ Railway
Gunicorn دون pre-deploy migration ودون DDL داخل Web workers؛ قاعدة Supabase
الحالية لا تتغير ضمن ترحيل الاستضافة.

## فحوص الإقلاع

```bash
curl --fail --silent "$RAILWAY_URL/healthz"
```

النتيجة المطلوبة:

```json
{"database":"ok","status":"ok"}
```

تحقق من logs أن Gunicorn هو الخادم، وأنه لا تظهر رسائل بدء schedulers.

## Smoke test

- Login بحساب اختبار.
- Reload مع بقاء session.
- Home وDiscovery وCase وDecision P0.
- رفض الوصول لشركة غير شركة حساب الاختبار.
- PDF عربي.
- restart واحد ثم إعادة `/healthz` وlogin/session.

## الفوترة

قبل DNS فعّل أقرب عتبات متاحة إلى 50% و75% و90%، واختبر وصول تنبيه واحد.
لا ترفع الخطة أو تضف مراقبة مدفوعة تلقائيًا.

## DNS

بعد نجاح الاختبارات فقط:

1. أضف `sanaclarity.com` إلى Railway.
2. انسخ target الرسمي.
3. احفظ سجل الويب القديم.
4. عدّل Web record المطلوب فقط لدى مزود DNS الحالي.
5. لا تغيّر Nameservers أو سجلات البريد.

## التراجع

أعد Web record القديم. لا تغيّر Supabase ولا تحذف Railway أثناء التحقيق.
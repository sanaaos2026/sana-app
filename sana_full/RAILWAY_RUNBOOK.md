# Railway Production Runbook

## الخدمة

- مشروع Sana الحالي أو مشروع جديد ضمن الحساب الحالي.
- خدمة Web واحدة، Replica واحدة، أقل موارد مناسبة، وخدمتا Cron مستقلتان
  لتنظيف الدفع وإعادة تنبيهات الفوترة.
- GitHub repo: `sanaaos2026/sana-app`.
- Branch: `main`.
- Root Directory: `sana_full`.
- Builder: Nixpacks.
- Healthcheck: `/healthz`.
- لا تُشغّل أي مجدول داخل Web workers. أنشئ خدمة Railway Cron مستقلة للتنظيف
  باستخدام ملف `railway.billing-cleanup.json` من جذر المستودع، وبمتغيرات
  قاعدة البيانات/Stripe نفسها الخاصة بخدمة Web.
- أنشئ خدمة Railway Cron ثانية لإعادة تنبيهات الفوترة باستخدام
  `railway.billing-notification-retry.json`، وبمتغيرات قاعدة البيانات وResend
  نفسها الخاصة بخدمة Web.

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
SANA_BILLING_CLEANUP_WORKER=0
SANA_BILLING_NOTIFICATION_RETRY_WORKER=0
```

لا تنسخ القيم إلى Git أو logs.
يجب أن يكون `SESSION_SECRET` عشوائيًا وثابتًا وطوله 32 محرفًا على الأقل؛
التطبيق يرفض الإقلاع بقيمة مفقودة أو قصيرة.

### خدمة تنظيف جلسات الدفع

أضف خدمة Railway ثانية من نفس المستودع، واجعل Root Directory فيها هو جذر
المستودع (وليس `sana_full`) واختر ملف الإعداد
`railway.billing-cleanup.json` لها. اضبط لها `SANA_ENV=production` و
`SANA_BILLING_CLEANUP_WORKER=1`، وشارك فقط متغيرات الإنتاج اللازمة للاتصال
بقاعدة البيانات وStripe. الجدولة `0 2 * * *` تعمل يوميًا بتوقيت UTC (05:00
بتوقيت الرياض)، والعامل ينتهي بعد تشغيل واحد. لا تضبط أي
`ENABLE_*_SCHEDULER=1` في خدمة Web أو خدمة التنظيف.

العامل يطبع JSON يحوي `status` والعدادات
`scanned` و`expired` و`already_completed` و`already_expired` و`failed` فقط؛
لا يطبع معرّفات Checkout أو Subscription. قفل PostgreSQL يمنع تشغيل نسختين
في الوقت نفسه، كما أن فشل Stripe ينهي التشغيل برمز غير صفري لتظهر المحاولة
الفاشلة في سجل الخدمة وتُعاد في الجدولة التالية. تُحفظ آخر نتيجة مكتملة في
`sana_billing_cleanup_runs`، ويُحفظ آخر تجاوز بسبب القفل بصورة مستقلة في
`sana_billing_cleanup_skips`. تظهر النتيجتان للمشرف في تبويب الباقة والدفع
مع وقت التشغيل والعدادات فقط؛ ولا يستبدل تجاوز القفل أو فشل Stripe آخر
تنظيف مكتمل.

### خدمة إعادة تنبيهات الفوترة

أضف خدمة Railway Cron مستقلة من نفس المستودع، واجعل Root Directory هو جذر
المستودع واختر `railway.billing-notification-retry.json`. اضبط لها
`SANA_ENV=production` و`SANA_BILLING_NOTIFICATION_RETRY_WORKER=1` وشارك
`DATABASE_URL` و`RESEND_API_KEY` و`SESSION_SECRET` نفسه المستخدم في خدمة
Web؛ استيراد منطق التطبيق يطبق شرط سر الجلسة الإنتاجي نفسه.
الجدولة `*/5 * * * *` تشغّل دورة واحدة كل خمس دقائق ثم ينتهي العامل.

لا تضبط أي `ENABLE_*_SCHEDULER=1` في هذه الخدمة. يطبع العامل الحالة والعدادات
فقط، دون بريد المستلم أو معرّفات Stripe أو أي بيانات دفع. قفل كل رسالة في
PostgreSQL يمنع خدمتين من إرسالها معًا، وتبقى المحاولات الفاشلة خاضعة للحد
الأقصى والتأخير المتزايد المحفوظين في صندوق الإشعارات.

قبل تفعيل الجدولة، ينفذ Railway تلقائيًا خطوة `preDeployCommand` المعرفة في
ملف الخدمة، وهي تشغّل
`python3 sana_full/billing_notification_retry_migration.py`. الترقية مقفلة
وآمنة للتكرار، وتضيف أعمدة المحاولات والمواعيد والقفل والفهرس إلى جدول
`admin_notification_outbox` الموجود دون حذف بياناته. يجب أن تنجح خطوة
`preDeploy` قبل السماح لأول تشغيل Cron؛ فشلها يوقف النشر ولا يبدأ العامل.

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
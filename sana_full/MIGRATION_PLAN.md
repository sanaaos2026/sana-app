# خطة الترحيل التدريجية: Railway Web + Supabase الحالية

## المرحلة A — تجهيز ونشر Railway بلا DNS

1. التأكد أن `main` في GitHub يحتوي النسخة المستقرة ولا أسرار متتبعة.
2. إنشاء/استخدام مشروع Railway وخدمة Web واحدة فقط.
3. ربط المستودع `sanaaos2026/sana-app` وتعيين Root Directory إلى
   `sana_full`.
4. استخدام Nixpacks و`railway.toml` وGunicorn؛ لا Railway Database ولا Worker.
5. ضبط الأسرار المطلوبة فقط:
   - `DATABASE_URL`: Supabase **Session Pooler** الحالي.
   - `SESSION_SECRET`.
   - `ADMIN_PREVIEW_KEY` عند الحاجة.
   - `APP_URL`: رابط Railway المؤقت في المرحلة A.
   - `SANA_ENV=production`.
6. ضبط:
   - `ENABLE_EXECUTION_REMINDER_SCHEDULER=0`
   - `ENABLE_KNOWLEDGE_BACKUP_SCHEDULER=0`
   - `ENABLE_KNOWLEDGE_RESEARCH_SCHEDULER=0`
7. لا pre-deploy migration ولا seed: قاعدة Supabase الحالية لا تتغير ضمن
   ترحيل الاستضافة.
8. نشر الخدمة وفحص `/healthz`.

**التراجع:** إيقاف خدمة Railway فقط؛ Replit وSupabase وDNS لم تتغير، لأن
النشر لا يشغّل DDL أو seed.

## المرحلة B — Smoke test آمن

استخدم حساب اختبار وبيانات اصطناعية فقط:

1. HTTPS وHTTP 200 وGunicorn.
2. اتصال قاعدة البيانات.
3. login ثم بقاء session بعد reload.
4. home وdiscovery وcase ومسار القرار الأساسي.
5. تحقق tenant isolation بمحاولة وصول حساب الاختبار إلى شركة أخرى.
6. PDF عربي.
7. تأكد من عدم بدء scheduler.
8. أعد تشغيل Railway مرة واحدة وكرر `/healthz` وlogin/session.

أي فشل يمنع DNS.

## المرحلة C — التنبيهات قبل التحويل

1. Railway: تنبيهات استهلاك/فاتورة عند 50% و75% و90% أو أقرب عتبات توفرها
   الخطة الحالية، بلا ترقية تلقائية.
2. Supabase: تنبيهات التخزين والاتصالات وصحة القاعدة والنسخ المتاحة.
3. اختبر وصول تنبيه واحد واحفظ إثباتًا بلا معلومات حساسة.

هذه بوابة إلزامية وليست عنصرًا مؤجلًا.

## المرحلة D — اكتشاف DNS وربط الدومين

1. اكتشف المسجل، مزود DNS، Nameservers، وسجلات root و`www`.
2. أضف الدومين الصحيح إلى Railway وخذ DNS target الرسمي منه.
3. احفظ Web record القديم ووجهته وrollback target.
4. لا تغيّر Nameservers أو سجلات البريد.
5. عدّل أقل Web record ممكن فقط بعد نجاح المراحل A–C.
6. اضبط `APP_URL=https://sanaclarity.com`.

## المرحلة E — تحقق وتراجع

بعد التحويل افحص DNS وHTTPS والشهادة والlanding وlogin وsession وhome
والروابط المولدة.

إذا فشل Railway:

1. أعد Web record إلى الوجهة القديمة.
2. لا تمس Supabase.
3. تحقق من Replit fallback.
4. سجل سبب الفشل دون بيانات عملاء أو أسرار.

## المرحلة F — إنهاء الاعتماد على Replit

راقب Railway مدة 48–72 ساعة. بعد الاستقرار فقط:

- أوقف نشر Replit الإنتاجي واترك Replit للتطوير.
- GitHub → Railway يصبح مسار النشر الوحيد.
- غيّر أي Supabase Cron يشير إلى Replit إلى الدومين النهائي، مع اختبار رفض
  الطلب غير المصادق ونجاح الطلب الداخلي المصادق.
- أبقِ النسخ الحالية قائمة حتى اعتماد مشغل بديل واحد؛ لا تشغل backup scheduler
  داخل Railway Web.

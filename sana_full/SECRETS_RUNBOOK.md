# دليل أسرار إنتاج Sana على Railway

## المصدر الوحيد لكل قيمة

| السر/المتغير | Railway Production | Replit Development |
|---|---|---|
| `DATABASE_URL` | Supabase Session Pooler الحالي | قاعدة التطوير فقط |
| `SESSION_SECRET` | قيمة إنتاج ثابتة مستقلة | قيمة تطوير مستقلة |
| `ADMIN_PREVIEW_KEY` | Secret مستقل إن فُعّلت المعاينة | قيمة تطوير مستقلة |
| `APP_URL` | Railway URL مؤقتًا، ثم `https://sanaclarity.com` | رابط التطوير |
| مفاتيح الميزات الاختيارية | تنقل فقط إن احتاجها P0 | تبقى للتطوير |

لا يستخدم الإنتاج `SANA_DATABASE_URL` أو `PG*` قديمة أو رابط `helium`.

## تحديث `DATABASE_URL`

1. خذ Connection string لـSupabase **Session Pooler** الحالي. لا تستخدم
   Transaction Pooler في هذا النشر.
2. أدخله مباشرة في Railway Variables باسم `DATABASE_URL` دون نسخه إلى ملف أو
   محادثة.
3. انشر/restart خدمة Railway.
4. تحقق من `/healthz` ومن login/session.
5. عند الفشل أعد القيمة السابقة من سجل Railway Variables؛ لا تغيّر البيانات.

لا تطبع الرابط كاملًا. يكفي في التشخيص تسجيل نوع الخطأ والحالة دون host أو
username أو password.

## تدوير الأسرار

1. أنشئ القيمة الجديدة في الخدمة المالكة.
2. حدّث Railway Secret Manager.
3. restart واختبار.
4. أبطل القيمة القديمة بعد نجاح الاختبار.

أي سر ظهر في Git أو logs أو المحادثة يعد مكشوفًا ويُدوّر، ولا يعاد استخدامه.

## الحد الأدنى للصلاحيات

- لا تنقل كل أسرار Replit إلى Railway.
- لا تجعل Google Drive Connector اعتماد تشغيل Railway.
- إن احتاج Drive لاحقًا، استخدم Service Account محدودًا بمجلدات Sana المطلوبة.
- مفاتيح AI والبريد اختيارية للمرحلة الأولى ما لم يكن مسار P0 المختبر يحتاجها.

## متغيرات Web-only

اضبط القيم التالية إلى `0` في Railway:

```text
ENABLE_EXECUTION_REMINDER_SCHEDULER
ENABLE_KNOWLEDGE_BACKUP_SCHEDULER
ENABLE_KNOWLEDGE_RESEARCH_SCHEDULER
```

لا تحفظ قيم الأسرار في `railway.toml` أو `Procfile` أو `nixpacks.toml`.
يكتشف التطبيق Railway تلقائيًا من متغيرات المنصة المدمجة ويرفض أي
`SESSION_SECRET` مفقود أو أقصر من 32 محرفًا أو scheduler مفعّل. غياب متغير
scheduler يعني `0` افتراضيًا. يبقى `SANA_ENV=production` طبقة صريحة إضافية،
وليس مفتاح الحماية الوحيد.

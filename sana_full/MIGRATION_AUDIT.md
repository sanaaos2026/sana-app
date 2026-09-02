# تدقيق ترحيل إنتاج Sana: Replit → Railway + Supabase

**النطاق:** فصل تشغيل `sanaclarity.com` عن رصيد ودورة تطوير Replit، دون نقل
قاعدة البيانات الحالية أو تعريض بيانات العملاء.

## القرار المعماري

| المكوّن | الدور النهائي | التكلفة والمخاطر | الإجراء |
|---|---|---|---|
| GitHub | مصدر الكود، فرع `main` | لا أسرار في Git ولا force-push | Railway ينشر تلقائيًا من المستودع الحالي |
| Railway | Web runtime فقط، خدمة واحدة ونسخة واحدة | Hobby يبدأ برسوم أساسية لكن الاستهلاك قد يرتفع؛ تجاوز الحدود قد يوقف الخدمة | أقل موارد معقولة، Gunicorn، `/healthz`، وتنبيهات قبل DNS |
| Supabase الحالية | Database of Record | لا نقل بيانات ولا Railway Postgres؛ الخطر هو اتصالات كثيرة أو سر قديم | استخدام Pooler الحالي عبر `DATABASE_URL` واحد |
| مزود DNS الحالي | توجيه الدومين | أي خطأ قد يقطع الويب أو البريد | تعديل Web record فقط بعد نجاح Railway؛ لا Nameservers ولا سجلات بريد |
| Replit | تطوير وخط رجوع مؤقت | لا يبقى اعتماد إنتاجي بعد الاستقرار | يبقى كما هو حتى 48–72 ساعة بعد التحويل |

## مقارنة مختصرة

### Railway مقابل Replit Autoscale

- **الفصل:** Railway يملك دورة نشر وفوترة مستقلة؛ Replit الحالي يربط الإنتاج
  ببيئة التطوير ونشرة قديمة.
- **التشغيل:** Railway يشغّل Gunicorn، بينما خادم Flask التطويري غير مناسب
  للإنتاج.
- **الكلفة:** ابدأ بخدمة Web واحدة وReplica واحدة، بلا Database أو Redis أو
  Worker أو إضافات مدفوعة.
- **الخطر الأكبر:** توقف الخدمة عند حد الاستهلاك؛ لذلك تنبيه 50% و75% و90%
  شرط قبل تحويل DNS.

### Supabase الحالية مقابل إنشاء قاعدة جديدة

- إبقاء Supabase يمنع نسخ بيانات العملاء ويزيل مخاطرة ترحيل البيانات.
- يستخدم التطبيق اتصالًا canonical واحدًا: `DATABASE_URL` عبر **Session
  Pooler**. لا يوجد توصية بديلة بـTransaction Pooler لهذا النشر.
- لا يستخدم `SANA_DATABASE_URL` ولا رابط Replit/helium في Railway.
- يستخدم Session Pooler مع SSL وإعداد اتصالات مناسب لعمال Gunicorn.

## جاهزية الكود

- `Procfile` و`railway.toml` يشغّلان Gunicorn على `$PORT`.
- `nixpacks.toml` يثبّت مكتبات WeasyPrint والخطوط العربية.
- `/healthz` يختبر PostgreSQL ويرجع 503 عند فشل الاتصال بلا كشف التفاصيل.
- Railway لا ينفذ migrations أو DDL أو seed تلقائيًا؛ Supabase الحالية تبقى
  كما هي، وعمال Gunicorn يقرؤون المخطط الموجود مسبقًا.
- التطبيق يرفض إقلاع Web production إذا فُعّل أي scheduler معروف.
- التطبيق يرفض إقلاع production بلا `SESSION_SECRET` صالح (32 محرفًا على الأقل).

## المخاطر والضوابط

1. **أسرار غير صالحة:** تنقل القيم فقط بين Secret Managers، ولا تسجل في Git
   أو logs. أي قيمة ظهرت في محادثة تعد compromised وتدوّر.
2. **جلسات غير مستقرة:** `SESSION_SECRET` ثابت وإلزامي في Railway.
3. **تعدد المستأجرين:** smoke test بحساب اصطناعي ويتحقق من رفض الوصول لشركة
   أخرى.
4. **PDF:** اختبار فعلي لـWeasyPrint قبل DNS.
5. **الجدولة:** لا scheduler داخل Web process منعًا للتكرار عند restart/scale.
6. **DNS:** لا تغيير Nameservers أو MX/SPF/DKIM/DMARC؛ حفظ السجل القديم قبل
   تعديل سجل الويب.

## بوابات الانتقال الإلزامية

لا DNS قبل تحقق الجميع:

1. Railway URL يعيد HTTPS و`/healthz` = 200.
2. اتصال Supabase وlogin/session/home ومسارات P0 ناجحة.
3. restart واحد ناجح ولا scheduler يبدأ.
4. تنبيهات تكلفة Railway واستهلاك/قاعدة Supabase مفعلة ومختبرة.
5. مزود DNS والسجل الصحيحان محددان بثقة، وهدف Railway مأخوذ من Railway نفسه.

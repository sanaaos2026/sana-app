# SANA P0 — CLARITY LOOP
## ملف التنفيذ والواجهة — Replit Implementation Spec

**الحالة:** P0 / أولوية قصوى
**الهدف:** إطلاق تجربة بسيطة جدًا تثبت وعد سنع الأساسي.
**المبدأ:** لا تعرض البنية الداخلية؛ اعرض القرار.

---

# 1. نتيجة P0 المطلوبة

خلال أول استخدام يجب أن يعرف العميل:
1. ماذا فهم سنع عن شركتي؟
2. أين المشكلة الأهم؟
3. لماذا؟
4. ما الدليل؟
5. ما الذي لا نعرفه؟
6. ما الأولوية؟
7. ماذا أفعل الآن؟

النتيجة الشعورية المطلوبة:
> **الآن فهمت أين المشكلة وماذا أفعل.**

---

# 2. رحلة P0 الوحيدة

Landing → Signup/Login → Company Setup → Company Research → Discovery → Hypotheses → Adaptive Questions → Evidence Requests → Sana Scan → Diagnostic Review → Top Bottleneck → Decision → Task → Impact Review

ممنوع إنشاء Journeys متوازية في P0.

---

# 3. الصفحة الأولى

العنوان:
> **اعرف أين يتعطل نمو شركتك، وماذا تفعل الآن.**

الوصف:
> سنع يجمع ما يعرفه عن شركتك، يختبره بالأدلة، ويحدد الاختناق الأعلى أولوية والقرار التالي.

CTA رئيسي:
> **ابدأ التشخيص**

CTA ثانوي اختياري:
> كيف يعمل سنع؟

لا تعرض قائمة منتجات طويلة أو أسماء المحركات أو Frameworks الداخلية.

---

# 4. Company Setup

اجمع فقط:
- اسم الشركة
- الموقع/المتجر
- القطاع
- وصف النشاط
- هدف 90 يوم
- أهم تحدٍ
- روابط إضافية اختيارية

Progress بسيط:
1/3 معلومات الشركة
2/3 ماذا تريد تحقيقه؟
3/3 أين ترى المشكلة؟

---

# 5. Company Research

أظهر:
> **سنع يجمع الصورة الأولية**

افحص:
- الموقع
- العروض
- القنوات
- التواجد العام
- الإشارات المهمة

النتيجة:
## ماذا فهمنا؟
3–7 Facts/Observations فقط

## ما الذي نحتاج تأكيده؟
2–5 Hypotheses

CTA:
> **أكمل التشخيص**

---

# 6. Discovery

ابدأ بـ10–15 سؤالًا أساسيًا بحد أقصى، ثم 5–12 سؤالًا تكيفيًا عند الحاجة.

كل شاشة:
- سؤال واحد
- سبب السؤال اختياري
- إجابة
- لا أعرف
- التالي

القاعدة:
> لا سؤال بدون Hypothesis أو Decision سيتغير بناءً على الإجابة.

---

# 7. Evidence Requests

إذا احتاج سنع إثباتًا:
> **نحتاج دليلًا واحدًا لنثبت هذه النقطة**

الخيارات:
- رفع ملف
- ربط مصدر
- إدخال رقم
- تخطي الآن

لا تطلب عدة ملفات دفعة واحدة بلا ضرورة.

---

# 8. Sana Scan

داخليًا يمكن فحص:
Strategy / Market / Offer / Marketing / Sales / Operations / Finance / People / Systems / Assets

لكن العميل يرى:
- أقوى 3 إشارات
- أهم اختناق
- قوة الدليل
- ما الذي لا يزال غير مؤكد

---

# 9. Today — الصفحة الرئيسية بعد التشخيص

## أهم شيء الآن
القضية/الاختناق.

## لماذا؟
سطران فقط.

## الدليل
2–4 Evidence Cards.

## ما الذي لا نعرفه؟
إذا وجد.

## القرار
قرار واحد.

## الخطوة التالية
CTA واحد:
> **ابدأ الآن**

لا تجعل Today Dashboard عامة.

---

# 10. Evidence Card

تعرض:
- نوع المصدر
- المعلومة
- التاريخ
- مستوى القوة
- View Source

مثال:
**CRM — قوي**
31 فرصة مؤهلة / شهر
آخر تحديث: 1 سبتمبر

---

# 11. Diagnostic Review — للمستشار فقط

جدول:
- Axis
- What We Know
- Source
- Client Answer
- Hypothesis
- Supporting Evidence
- Contradicting Evidence
- KPI
- Confidence
- Finding Status
- Decision Impact

فتح Finding يعرض:
Source → Evidence → Question → Answer → Hypothesis → Rule → KPI → Conclusion

---

# 12. حالات Finding

- VERIFIED
- SUPPORTED
- PARTIAL
- DEFERRED
- REJECTED

---

# 13. Top Bottleneck

اعرض اختناقًا رئيسيًا واحدًا.

مثال:
**الاختناق:** ضعف التحويل بعد تقديم العرض
**لماذا:** عدد الفرص كافٍ لكن الإغلاق منخفض
**الدليل:** 31 فرصة → 8 عروض → 1 إغلاق
**الأثر:** تسرب إيرادي
**الثقة:** قوي/متوسط/ضعيف
**ما نحتاجه:** بيانات Follow-up إن كانت ناقصة

---

# 14. Decision

كل Decision يحتوي:
- What
- Why
- Owner
- Deadline
- KPI
- Baseline
- Target
- Next Action

ممنوع Approve بدون Owner + Deadline + KPI + Next Action.

عند الاعتماد:
Decision + Task Creation كعملية واحدة.

---

# 15. Task

اعرض:
- ماذا؟
- من؟
- متى؟
- ما المقياس؟
- ما الدليل المطلوب عند الإكمال؟

لا تعرض Board ضخم في P0.

---

# 16. Impact Review

- Baseline
- Target
- Actual
- Evidence
- Result
- What Changed?
- What Did We Learn?
- Next Decision

---

# 17. التنقل

للعميل:
1. اليوم
2. فرص التحسين
3. المبيعات — عند الحاجة
4. أصول الشركة
5. المراجعة

للمستشار/Admin:
- Diagnostic Review
- Knowledge
- Research
- Admin

---

# 18. دمج الشاشات الحالية

MERGE:
- Passport → Scan / Assets
- Scan Report → Executive Report
- Next Step → Case
- Tasks Board → Execution
- Assessment → Scan
- Decision Room → Today / Case
- SOP → يظهر داخل التنفيذ عند الحاجة

INTERNAL:
- Knowledge Console
- Research Library
- Methodology
- Deal Brain
- Admin
- Backups
- Scheduler

LATER:
- Growth OS expanded
- Expert Portal
- Marketplace
- Gamification
- Mobile App
- Full CRM

---

# 19. مبادئ UX

1. شاشة واحدة = قرار واحد
2. CTA رئيسي واحد
3. Progressive Disclosure
4. لا أكثر من 3–5 عناصر أساسية في الواجهة الأولى
5. لا Scores كثيرة
6. لا أسماء محركات داخلية
7. لا تفاصيل تقنية للعميل
8. العربية أولًا
9. الإنجليزي عند الحاجة فقط
10. اعرض: ما نعرفه / ما لا نعرفه / ماذا نفعل

---

# 20. معيار الخمس ثواني

كل شاشة تجيب خلال ≤5 ثوانٍ:
- أين أنا؟
- ما المهم؟
- لماذا؟
- ماذا أفعل الآن؟

---

# 21. Empty States

بدل "لا توجد بيانات":
> لا نملك دليلًا كافيًا بعد. أضف التقرير المطلوب أو أجب عن السؤال التالي لنكمل التشخيص.

كل Empty State له Next Action.

---

# 22. حالات الثقة

لا تستخدم نسبًا غير موثقة.

استخدم:
- قوي
- متوسط
- ضعيف

---

# 23. التقرير التنفيذي

الترتيب:
1. وضع الشركة الآن
2. أهم اختناق
3. لماذا هو مهم
4. الأدلة
5. السبب المرجح
6. ما لا نعرفه
7. الأولوية
8. القرار
9. KPI
10. الخطوة التالية
11. خطة 30/60/90 عند الحاجة

---

# 24. Company Research Data Types

- FACT
- CLAIM
- OBSERVATION
- HYPOTHESIS
- UNKNOWN
- CONTRADICTION
- EVIDENCE

كل URL = Evidence Source.

---

# 25. P0 Data Relationships

Company → Sources → Facts/Claims/Observations → Hypotheses → Questions → Answers → Evidence → Scan → Findings → Bottleneck → Decision → Task → Impact

---

# 26. Security P0

راجع:
- Authentication
- Session
- Tenant Isolation
- Roles
- CSRF
- PUBLIC_ENDPOINTS
- Preview/Admin separation
- Cookies
- Audit Log
- Retention
- Delete Policy

لا تعرض company_id أو admin_key أو preview params أو internal view params للعميل.

---

# 27. Technical Rules

قبل أي تعديل:
1. افحص الموجود
2. أعد استخدام ما يعمل
3. لا Rewrite شامل
4. لا Feature خارج P0
5. اذكر الملفات المتأثرة
6. اذكر المخاطر
7. اذكر اختبار القبول
8. لا تحذف Legacy قبل التحقق

---

# 28. P0 Acceptance Tests

1. Signup → Company Setup → Discovery → Scan → Decision يعمل كاملًا.
2. كل Finding رئيسي يملك Evidence أو يظهر كغير مؤكد.
3. كل Decision معتمد يملك Owner + Deadline + KPI + Next Action.
4. Today يعرض قضية رئيسية واحدة.
5. لا يستطيع عميل الوصول إلى بيانات عميل آخر.
6. العميل لا يرى Admin/Knowledge internals.
7. لا يوجد /case/null أو Case غير صالح.
8. Landing CTA يذهب للتسجيل الصحيح.
9. لا توجد Onboarding/sector selection مكررة.
10. Passport/Scan/Report لا تظهر كمنتجات متنافسة.

---

# 29. Definition of Done

شركة → روابط → Research → Discovery → Hypotheses → Adaptive Questions → Evidence → Sana Scan → Finding موثق → Top Bottleneck → Decision → Owner + Deadline + KPI → Task → Impact Review

ويستطيع المستشار الضغط على:
> **لماذا قال سنع هذا؟**

ويرى أصل الاستنتاج.

---

# 30. ما نؤجله

- Full Growth OS
- Full Sales CRM
- Public Deal Network
- Marketplace
- Mobile App
- Advanced Gamification
- Large Benchmark Engine
- Multiple AI Agents

---

# 31. معيار نجاح P0

النجاح ليس عدد الصفحات.

النجاح:
> المستخدم يخرج بوضوح أعلى وقرار أفضل وخطوة تالية قابلة للتنفيذ.

**سنع — وضوح يصنع النمو.**

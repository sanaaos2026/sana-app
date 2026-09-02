# TASK — SANA P0 FREEZE & IMPLEMENTATION

اقرأ أولًا الملفين:
1. SANA_PRODUCT_KNOWLEDGE_CORE.md
2. SANA_P0_IMPLEMENTATION_UI_SPEC.md

اعتبرهما المرجع الحاكم للتطوير الحالي.

## المطلوب

نفّذ Audit سريع على المشروع الحالي ثم طابقه مع P0.

لا تعمل Rewrite شامل.

### المرحلة 1 — Gap Audit
أعطني جدولًا:
- Current Screen / Feature
- Keep
- Merge
- Hide/Internal
- Fix
- Remove from P0
- Missing
- Risk
- Priority

### المرحلة 2 — P0 Freeze
جمّد Scope على:
Landing
Signup/Login
Company Setup
Research
Discovery
Adaptive Questions
Evidence
Scan
Diagnostic Review
Today
Decision
Task
Impact Review

أي شيء غير ذلك لا يبنى الآن إلا إذا كان Dependency مباشر.

### المرحلة 3 — UX Simplification
طبّق:
- one screen = one decision
- one main CTA
- progressive disclosure
- Arabic-first
- no internal engine names
- no dashboard clutter
- Today = أهم شيء الآن
- Evidence visible
- Unknowns visible
- one top bottleneck
- one next action

### المرحلة 4 — Fix Critical Flow
راجع خصوصًا:
- Landing CTA → Signup
- duplicate onboarding/sector
- discovery/case duplication
- Passport/Scan Report overlap
- competing CTAs in Case
- Growth OS exposure
- auth/session protection
- decision approval validation
- atomic task creation
- /case/null
- unsupported valuation
- admin/preview isolation

### المرحلة 5 — Knowledge Integration
اربط:
Drive Source → Knowledge Source → Knowledge Object → Evidence → Finding → Decision

ولا تحول Drive كله إلى knowledge.

### المرحلة 6 — Acceptance
لا تعتبر P0 جاهزًا حتى تنجح اختبارات القبول الموجودة في ملف P0.

## قاعدة التنفيذ

كل تعديل يجب أن يذكر:
- المشكلة
- لماذا هو ضروري لـP0
- الملفات التي ستتغير
- طريقة الاختبار
- النتيجة

ولا تضف أي Framework أو Feature جديد بدون Gap حقيقية مثبتة.

## المخرجات المطلوبة الآن قبل البرمجة

1. P0 Gap Audit
2. Current vs Target Journey
3. Screens to Keep/Merge/Hide
4. Database/Data gaps
5. Security gaps
6. Knowledge integration gaps
7. Ordered implementation tasks P0 فقط
8. Estimated effort لكل Task
9. Dependencies
10. Definition of Done

بعد إعطاء التقرير:
ابدأ التنفيذ من أعلى أولوية فقط.

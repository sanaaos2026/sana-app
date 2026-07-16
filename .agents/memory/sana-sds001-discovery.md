---
name: Sana SDS-001 Discovery Session
description: Architecture and constraints for the onboarding discovery flow (جلسة الاكتشاف)
---

## Rule
SDS-001 is a one-time 8-question onboarding flow shown immediately after signup+onboarding (company name/sector). It replaces the empty home-page first state.

## Flow
signup → `/onboarding` (name+sector+employee_count) → `/discovery` (SDS-001) → `/case/<case_id>` (first auto-created case)

Login also checks `sds_done`: if 0, redirects to `/discovery` before home.

## DB
- `companies.sds_done` SMALLINT DEFAULT 0 — set to 1 when save API called.
- `companies.main_goal` — Q1 answer.
- `companies.vision` — Q8 answers joined with "، ".
- First case created from Q2 in `cases` table (case_type="تشخيص", case_status="مفتوح").
- All Q answers stored as evidence (`source_type="اكتشاف_ذاتي"`, `confidence=0.5`).

## Asset mapping (Q3/Q4/Q5 → asset_type)
- Brand: سمعة الشركة, العلاقات, البراند
- Independence: المؤسس, الفريق (Q3); Q5 evidence
- Knowledge: المنتج أو الخدمة, الخبرة والمعرفة
- Operations: السعر (Q3); Q4 acquisition evidence

## Idempotency
`/api/discovery/save` checks `sds_done` first — if already done, returns existing first case_id without re-inserting.

## Constraints (from spec)
- No score or "weakest asset" shown in outro screen (SCORE-03 rule).
- Q7 (value estimate) stored silently — NO hint to user about future comparison.
- Q4 follow-up "لا"/"إلى حد ما" gets tag "[تنبيه: خطر الاعتماد على قناة واحدة]" in evidence title.
- No new tables — uses existing `cases` + `evidence`.
- Case from Q2 must NOT duplicate if user revisits page (sds_done guard).

## Admin preview
`/discovery?admin_key=<KEY>&company_id=<CID>` sets session and renders the page (debug/admin bypass).
`?step=N` in the template renders a specific question directly (dev preview param).

**Why:** The discovery page requires session auth but the screenshot/testing tools cannot inject cookies. The admin_key bypass is a standard pattern already used in other routes.

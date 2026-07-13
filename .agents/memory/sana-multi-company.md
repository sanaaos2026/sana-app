---
name: Sana multi-company support
description: How multiple companies are viewed in the Sana Flask app (sana_full/), since the UI was originally hardcoded to one company.
---

The Sana backend (`app.py`) was always multi-tenant at the data/API layer — every route is parameterized by `company_id` and cases/assets/decisions/evidence/tasks all key off it. But the frontend templates hardcoded `const COMPANY_ID = "C001"` in their `<script>` blocks, so only the seeded company ("أثر مشرق") was ever reachable through the UI.

**Decision:** rather than building a company switcher UI (out of scope when this was needed), each company-scoped template reads `COMPANY_ID` from a `?company_id=` URL query param, falling back to `"C001"` when absent. This keeps default behavior unchanged for existing links/bookmarks.

**Why:** the ask at the time was just "add a new company's data" (a law-firm client, C002, classified under a custom `case_type` "مسار الإنقاذ السريع" / Rescue Track) — but with COMPANY_ID hardcoded, the new company would have been invisible in the UI even though its data was correctly seeded. The query-param approach was the minimal, non-breaking way to make newly seeded companies actually viewable.

**How to apply:** to view/manage any non-default company, append `?company_id=<id>` to `/home`, `/passport`, `/case/new`, `/sop-builder`, `/assessment`. The `/case/<case_id>` route already took the case id from the URL path (not a hardcoded constant), so it needed no change. Dynamic in-page links (e.g. "افتح القضية الكاملة", "جواز الشركة") on `/home` propagate the current `company_id` via a `CQS` query-string helper built from `COMPANY_ID`; static top-nav "concept-nav" links remain hardcoded to C001/CS001 by original design (a fixed demo nav, not a functional breadcrumb) and were left as-is.

If a future company needs a case-workspace "case_id" default, note that `/case/CS001` was hardcoded on the passport page pointing to the weakest asset's case — this was fixed to derive the real weakest-asset case id from a new `weakest_asset_case_id` field added to the `/api/companies/<id>/passport` response (queries `cases` by `related_asset_id`), rather than assuming CS001.

New companies can be seeded with a one-off script pattern like `sana_full/seed_c002.py`: insert directly into `companies`, `users`, `assets` (5 rows: Knowledge/Operations/Brand/Data/Independence — these exact `asset_type` values are required for `DECISION_TEMPLATES` lookups to work), `cases`, `evidence`, `decisions`, `decision_asset_impacts`, and `tasks`. Guard with an existence check on the company_id so reruns are safe.

---
name: Sana Flask app auth & tenant isolation model
description: How real customer login coexists with the legacy ?company_id= admin demo mechanism in sana_full, and where cross-tenant checks live.
---

Sana (`sana_full/`) has two separate access modes on the *same* routes (`/home`, `/case/*`, `/passport`, etc. and their `/api/*` counterparts):
1. Real customer session (`session["company_id"]` set by `/login` or `/signup`) — the only source of truth for a logged-in user's company.
2. Internal admin preview — `?admin_key=<ADMIN_PREVIEW_KEY>` query param matching the `ADMIN_PREVIEW_KEY` shared env var. Grants the old unauthenticated `?company_id=` behavior, but only to whoever holds the key (never published in any template/link).

**Why:** the app originally trusted an editable `?company_id=` query param for everyone — a full cross-tenant data leak. The fix had to keep an internal multi-company browsing tool for the admin without exposing it to real customers, and without customers being able to view other companies' data by any means (including calling `/api/...` JSON endpoints directly, not just page URLs).

**How to apply:**
- `app.py`'s `before_request` (`enforce_company_auth`) denies *everything* except an allowlist (`PUBLIC_ENDPOINTS`) unless the request has a real session or the admin key — this covers API endpoints too, not just HTML pages, since anonymous direct API calls were the actual leak vector.
- For endpoints where `company_id` isn't in the URL (case/evidence/decision/task by bare ID), each handler must fetch the row first and call `enforce_entity_company_scope(row["company_id"])` before returning data — the blanket `view_args` check only catches routes with `company_id` literally in the path.
- Signup requires a per-company `signup_code` (on `companies` table) — self-service signup never lets a user pick/type a `company_id` directly.
- Templates preserve `admin_key` (alongside the pre-existing `company_id`/`view` query params) through their `CQS`/`qs()` builders and a small concept-nav rewrite script, so admin-preview navigation keeps working end-to-end.

System-level `SUPER_ADMIN` accounts are the only accounts allowed to have no company membership. A company-less `SUPER_ADMIN` must be routed to the Command Center and denied by ordinary tenant routes; cross-company access stays limited to explicit, audited admin endpoints. Every `USER` or `ADMIN` account must remain bound to a company.

**Why:** Leadership administration is a system responsibility, not membership in a customer company. Reusing a customer membership for global administration misstates ownership and risks accidental tenant-context access.

**How to apply:** Keep the database constraint that permits a null company only for an active admin-marked `SUPER_ADMIN`. Never treat the absence of a company as permission to choose a default tenant.

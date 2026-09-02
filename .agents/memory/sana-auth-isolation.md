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

System roles (`ADMIN`, `SUPER_ADMIN`) are not company memberships. Both may be company-less; a company-less system admin is routed to the Command Center and denied ordinary tenant routes until it explicitly opens a company context. Company roles (`COMPANY_OWNER`, `COMPANY_MEMBER`) remain tenant-bound. `SUPER_ADMIN` has all management powers, while `ADMIN` receives only an explicit allowlist of management permissions.

**Why:** Leadership administration is a system responsibility, not membership in a customer company. Reusing customer membership for global administration misstates ownership; implicit tenant selection or broad default Admin powers risks accidental cross-tenant access.

**How to apply:** Keep the database constraint that permits a null company only for admin-marked system roles. Never treat the absence of a company as permission to choose a default tenant. Require an explicit audited “open company” action, and gate every Admin mutation by its named permission.

Company invitations are activation credentials, not displayable records: persist only a token hash, rotate it on resend, enforce expiry/single use/cancellation, and never return the token through admin APIs, logs, or notification outbox payloads.

**Why:** Command Center operators need to issue and manage invitations without gaining access to a user's password or reusable activation secret.

**How to apply:** Email may contain the one-time plaintext link during delivery, but durable database/admin surfaces retain only its hash and non-sensitive delivery metadata. Password resets use the existing one-time reset mechanism and are audited.

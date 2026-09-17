---
name: Sana test journey reset
description: Safety and performance boundaries for returning one test company to the start of its user journey.
---

Reset access is limited to durable Pilot membership slots 1–20 or system administrators, including in production. Membership is count-based, never date-based; existing real company accounts are seeded deterministically, and new accounts receive the next free slot until the cap. The reset acts only on the company bound to the authenticated session and runs as one transaction. Preserve the authentication account, company identity shell, subscriptions, billing events, invitations, and audit history.

**Why:** A reset is intentionally destructive, but testers need to repeat onboarding without creating accounts. Per-table metadata checks against a remote PostgreSQL database made the operation unacceptably slow, while a partial failure would leave an invalid journey state.

**How to apply:** Check the persisted Pilot number or admin role on every page render and reset request; never grant access from dates or environment flags. Keep a fixed allowlist of journey/execution tables, batch tenant-scoped deletes, reset only setup/progress fields, and roll back on any error.
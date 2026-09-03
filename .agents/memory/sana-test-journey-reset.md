---
name: Sana test journey reset
description: Safety and performance boundaries for returning one test company to the start of its user journey.
---

The test reset must remain unavailable to ordinary production users, act only on the company bound to the authenticated session, and run as one transaction. Preserve the authentication account, company identity shell, subscriptions, billing events, invitations, and audit history.

**Why:** A reset is intentionally destructive, but testers need to repeat onboarding without creating accounts. Per-table metadata checks against a remote PostgreSQL database made the operation unacceptably slow, while a partial failure would leave an invalid journey state.

**How to apply:** Keep a fixed allowlist of journey/execution tables, batch their tenant-scoped deletes into one database statement, reset only setup/progress fields on the company shell, and roll back the entire operation on any error.
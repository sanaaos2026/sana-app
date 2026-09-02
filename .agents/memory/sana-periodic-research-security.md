---
name: Sana periodic research security boundary
description: Security and governance rules for any external knowledge discovery or monitoring.
---

Periodic research may fetch only from domains already qualified in the canonical source registry. Search-provider claims are discovery hints, not eligibility evidence; persist evidence and recheck it at human approval.

**Why:** External discovery creates both privacy and SSRF risks. A hostname-only check is insufficient because DNS can change between validation and connection, and authenticated redirects can leak provider credentials.

**How to apply:** Pin the validated public IP to the actual connection while preserving Host/SNI, revalidate every redirect, never forward sensitive headers across origins, exclude quotation-only rights from full-page storage, and keep every candidate outside approved search until review.
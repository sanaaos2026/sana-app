---
name: Sana returning-company check-ins
description: Product rules for incremental reassessment of a returning company.
---

Returning companies should not repeat first-time discovery by default. In the
P0 check-in, ask at most three questions: prior problem change, prior task
execution, and current value for the one prior KPI when available. Fresh,
non-conflicting context may be skipped; stale context is confirmed before
asking for a replacement.

**Why:** Repeating broad discovery wastes the owner's time and ignores what Sana
already learned, while blindly trusting old context can keep the wrong priority
active.

**How to apply:** Keep answers self-reported and preserve their existing trust
boundary. After the P0 questions, return to the existing case decision flow.
Calculate numeric Before/After/Change only for non-overlapping periods with
comparable duration, seasonality, and unit. A numeric increase is not
automatically an improvement because KPI direction may be unknown. Defer
multi-metric, recurring, and sector-specific follow-up to P1.
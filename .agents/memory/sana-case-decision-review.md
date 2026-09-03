---
name: Sana case decision review
description: Durable product rule for optional expert review of case decisions.
---

Expert review is an optional layer beside the case report and decision. It must
never block report access, downloading, or the existing decision journey.
Preserve the original decision as the immutable “before” snapshot; store the
reviewed conclusion, KPI, next action, and reason for any change as a separate
“after” snapshot with status history.

**Why:** The assisted V1 needs visible human accountability without turning
review into a hidden requirement or destroying the diagnostic record that led
to the original decision.

**How to apply:** New review features should use internal reviewer slots and
existing notifications, prevent double-booking, remain tenant-bound, and avoid
advertising a paid path until a complete payment flow exists.
---
name: Sana case decision review
description: Durable product rule for optional expert review of case decisions.
---

Expert review is an optional layer beside the case report and decision. It must
never block report access, downloading, or the existing decision journey.
Preserve the original decision as the immutable “before” snapshot; store the
human observation and any approved summary as a separate review record with
status history. Human notes never become Fact or Finding automatically.

**Why:** The assisted V1 needs visible human accountability without turning
review into a hidden requirement or destroying the diagnostic record that led
to the original decision. A deterministic summary prevents the formatting step
from inventing a root cause, KPI, result, or unsupported certainty.

**How to apply:** Client review is a simple optional request, not booking,
payments, or chat. Format summaries only from stored case data and reviewer
notes, label them Expert Observation / Human Review, expose them to clients only
after ADMIN or SUPER_ADMIN approval, and keep every action tenant-bound/audited.
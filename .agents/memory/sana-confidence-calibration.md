---
name: Sana confidence calibration
description: Durable rules for source-family confidence, verification, independence, and conditional decisions.
---

Preserve the stated confidence of self-reported and unverified information instead of converting it to zero. Keep source reliability, verification status, independence, and decision confidence as separate dimensions. Repeated statements from one source family count once, using the family's lowest usable confidence; the case statement itself is traceable context, not a confidence source.

**Why:** Self-reported operational data can support a bounded, low-risk next step without becoming a verified fact. Counting repeated statements or the declared problem as independent support would manufacture confidence, while rejecting all unverified information would trap the client in an evidence loop.

**How to apply:** Derive decision confidence conservatively from distinct source families without averaging. Allow consistent medium-confidence data to produce a clearly conditional 7–14 day measurement experiment with a KPI and no causal claim. Keep high-impact execution behind stronger verification and independence.

Client reports must translate readiness to جاهز/مشروط/غير جاهز across قبل القرار، القرار، وقياس الأثر. Show numeric zero as a real score, render missing scores as غير متاحة بعد, and never expose provenance codes or internal classifications.

**Why:** The internal confidence model has more dimensions than a customer needs, while hiding a true zero or inventing an impact baseline misstates the decision.

**How to apply:** Keep the internal evidence and readiness payload intact, but build a separate client view for HTML and text/PDF exports. An impact stage remains غير جاهز until a documented impact review exists.
---
name: Sana revenue cycle
description: Durable boundaries for deal identity, stage compatibility, delivery idempotency, and sourced commercial metrics.
---

Keep `opportunities` as the single deal identity. Extend the six stored Arabic B6 stages through a configurable canonical stage mapping rather than renaming old records or creating a parallel deal table.

**Why:** Existing B6 contracts and historical data depend on the Arabic stage names, while delivery, collection, retention, and upsell require a longer lifecycle.

**How to apply:** New commercial records must link to `opp_id` and `company_id`. A won transition creates one delivery task and one linked project, even when retried.

Commercial metrics may use only records classified as `Fact` with a real source reference and observation period. Missing evidence remains `N/A — Deferred`.

**Why:** Revenue, margin, collection, and learning must not become inferred company facts.

**How to apply:** Require source references for economics, invoices, payments, and deal learning; preserve conflicts and assumptions without including them in factual aggregates.
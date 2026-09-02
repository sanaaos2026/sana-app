---
name: Sana Growth OS evidence gate
description: Durable rule for using operational facts and baselines in downstream growth decisions.
---

An operational baseline is usable only when every required metric has a numeric value, an explicit source, an observation date, and a valid confidence score. Missing metrics remain `N/A — Deferred` and make the baseline incomplete; they must never be converted to zero or silently used by experiments and decisions.

**Why:** Growth recommendations must remain traceable to evidence and must distinguish unavailable data from measured zero.

**How to apply:** Downstream bottleneck, experiment, revenue-learning, and decision flows should call the shared baseline usability guard before accepting a baseline identifier.

Experiment outcomes must not infer that a higher KPI is always better. Metrics such as CAC and sales cycle improve by decreasing, while revenue usually improves by increasing; success/stop boundaries and the final decision require measured evidence and human review.

**Why:** A generic numeric comparison can reverse the meaning of a real result and manufacture a false recommendation.

**How to apply:** Store the actual result and target without auto-selecting Scale/Modify/Hold/Kill. Keep the decision explicit, sourced, and restricted to the approved four states.
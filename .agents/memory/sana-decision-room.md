---
name: Sana decision room
description: Durable governance rules for the executive decision-and-execution room.
---

The home screen must remain a deterministic eight-section view: current state, three metrics, primary bottleneck, top opportunity, running experiment, today's decision, next accountable task, and urgent risk. Missing or weakly sourced values remain `N/A — Deferred`.

**Why:** The product must connect evidence to one decision and one executable next step without turning incomplete data into confidence, scores, or recommendations.

**How to apply:** Treat existing decisions, tasks, experiments, and opportunities as the records of truth. Backlog ideas are isolated from active priority until explicit approval. A task is execution-ready only with Owner, Approver, Deadline, KPI, and status. SOP maturity starts Manual; standardization or automation readiness requires sourced results, explicit approval, and at least two successful repetitions.

For SOP repetitions, a submitted repetition count is not proof. Each application must identify the exact SOP version, a company-owned usable Baseline, a canonical KPI, the KPI direction, and a sourced observed result. Compute the comparison; do not infer whether higher or lower is better.

**Why:** A generic result note or user-entered repetition count can make an unproven process appear automatable and can mix evidence from different companies or versions.

**How to apply:** Count only improved applications with distinct execution references and non-duplicated outcome fingerprints. Require two for the same company/version before Automatable; reject foreign or reused proof.

Execution reminders must remain internal and must never change source status or priority. A human-readable owner is not an authenticated recipient; multi-account companies require an explicit, company-scoped owner-to-account assignment.

**Why:** Owner labels are not necessarily account identities. Guessing the recipient can hide an alert from its owner or expose it to the wrong account.

**How to apply:** Scope inbox reads and updates to the resolved recipient account. If ownership is ambiguous, keep the failed delivery visible to administrators and require an explicit assignment before delivery.

P0 completion must append one sourced Result and one explicit Impact Review against the captured baseline; the historical review is immutable and must not auto-change asset scores.

**Why:** Marking a task complete or applying an expected score delta is not proof that the business outcome improved, and overwriting the review would erase the decision trail.

**How to apply:** Close a P0 task only with a result summary, source reference, impact outcome, and reviewer note. Keep unsupported financial or growth effects `N/A — Deferred`; change scores only through separately qualified evidence.

Human review is bound to an exact diagnostic snapshot, not merely to a case. A rescan creates a new review obligation and must never inherit completion or decision linkage from an older snapshot.

**Why:** Treating P0 review as one-per-case can make a newer result appear reviewed when only an older evidence set was approved, or can trap the newer snapshot behind an unrelated prior decision.

**How to apply:** Make review and decision idempotency company-, case-, and snapshot-scoped. Preserve older decision links, and keep unresolved asset scores and financial value `N/A — Deferred` even after the current snapshot's human review completes.
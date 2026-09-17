---
name: Stripe lifecycle ordering
description: Durable safety rules for synchronizing Stripe subscription state without cross-tenant corruption or stale reactivation.
---

Stripe lifecycle processing must combine event-ID deduplication with per-subscription ordering. Cancellation is terminal for one Stripe subscription identity; an older or delayed paid event must not revive it.

**Why:** Stripe retries and may deliver different valid events out of order. A unique event ledger prevents duplicate application but does not prevent a delayed `invoice.paid` from overwriting a newer cancellation.

**How to apply:** Lock the local subscription while comparing Stripe event creation times, retain the newest accepted event marker, and prefer more restrictive states for equal timestamps. A new activation after cancellation must use a new Checkout/subscription identity.

Never use a matching Stripe customer alone to replace a different stored subscription ID. Early lifecycle events may bind only to an explicitly pending local Checkout row using company metadata placed on the Stripe subscription.

**Why:** One Stripe customer may own successive or concurrent subscriptions, so customer-only correlation can mutate the wrong tenant subscription.

**How to apply:** Prefer exact subscription-ID matches. Accept metadata correlation only for a pending row whose stored identifiers are empty or consistent; leave unmatched events retryable instead of permanently marking them processed.

Stale Checkout cleanup must re-read each locally pending session from Stripe while holding its subscription row lock, process each session in its own transaction, and only mark it canceled after Stripe confirms expiration.

**Why:** A browser return, webhook, or payment completion can race with an administrative cleanup run; local age alone cannot prove that the remote session is still unpaid and open. Batch-wide locks also let one Stripe timeout block later payment attempts on unrelated or already-failed sessions.

**How to apply:** Select candidates without locking the batch, then lock and re-read one row per transaction. Commit every confirmed/no-op result and roll back every provider failure immediately before moving on. Expose counts rather than Stripe identifiers.

Production stale-Checkout cleanup belongs to one-shot external Cron ownership,
not to Flask web workers. The worker must claim a database advisory lock and
emit only redacted counters before exiting.

**Why:** Railway Web can run multiple Gunicorn processes and replicas, while
an in-process daily loop would either duplicate Stripe work or stop with a web
restart.

**How to apply:** Keep production web scheduler flags off, deploy the cleanup
worker as a separate daily service, and treat a locked run as a successful
no-op.
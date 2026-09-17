---
name: Supabase RLS under live traffic
description: Safe rollout order when Sana's shared Supabase database has concurrent long-running transactions.
---

On Sana's live shared Supabase database, a single transaction that enables RLS
across every table can repeatedly deadlock with long application or test
transactions. Close the exposure first by revoking `anon` and `authenticated`
table privileges, then enable RLS and its deny policy in short per-table
transactions, retrying only locked tables.

**Why:** An all-table transaction held locks on earlier relations while active
business transactions needed those relations and held locks on later ones.
Every failed attempt rolled back safely, but could not converge under continuous
traffic. Short per-table transactions completed without terminating sessions or
stopping the service.

**How to apply:** Keep the canonical migration idempotent for maintenance
windows. For a live rollout, verify client grants are zero first, then process
only tables missing RLS/policy with a short lock timeout and multiple passes.
Finish with one metadata audit; do not rerun application journeys once the
targeted checks have passed.
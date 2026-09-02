---
name: Sana PostgreSQL startup DDL
description: Concurrency constraint for schema initialization shared by Flask, schedulers, and tests
---

Schema compatibility migrations must be serialized when more than one Sana process can initialize the PostgreSQL database at once. A Flask debug reloader, scheduled workers, and validation tests may overlap, and PostgreSQL relation-level DDL locks can deadlock with ordinary inserts or cleanup. Startup schedulers must begin only after schema initialization and seeding finish.

**Why:** The same database is used by the running app and validation processes, so a migration that is safe in a single process can block or deadlock under concurrent initialization. Starting a scheduler during seeding creates an avoidable second writer, while an unbounded retry would still tie reliability to another transaction's duration.

**How to apply:** Keep migrations idempotent, run them behind a transaction-scoped advisory lock with a bounded lock timeout, retry transient lock failures only a finite number of times, and start background schedulers after seeding. Test schema-changing startup separately from steady-state queries. Test-created companies should also include a signup code so startup backfills do not contend with the test transaction.

Development workflows that run Flask in debug/reloader mode can restart when test files change and leave pooled `idle in transaction` sessions behind; isolate database tests from that reloader before interpreting schema-lock failures.

**Why:** The lock failure can be environmental rather than a migration defect, and concurrent test/reloader sessions can obscure the actual application result.

**How to apply:** Keep the production lock behavior unchanged; stop or isolate the development workflow while running PostgreSQL tests, then restart it and verify clean startup logs.

Supavisor can also retain an idle transaction after a failed or interrupted unittest process, including a session that still owns the schema advisory lock. A later clean test run may then fail during setup until that stale test session is gone.

**Why:** Closing the local Python process does not always immediately release the pooled server-side session, so a second run can report a misleading lock timeout or deadlock before executing any test.

**How to apply:** Inspect active transactions before rerunning; wait for or terminate only the stale validation session created by the current run, never an unrelated application session.

PostgreSQL concurrency tests that synchronize request entry must apply their barrier only to each connection's first attempt. A retry must start immediately after rollback rather than waiting for the other request to retry too.

**Why:** A real serialization failure sends only the losing transaction through the retry loop. Reusing the two-party barrier on that retry turns a valid recovery into a test-only timeout or broken barrier.

**How to apply:** Track attempts per backend connection, synchronize attempt one, assert backend PIDs are distinct, and verify every request connection is transaction-ready before closing it.

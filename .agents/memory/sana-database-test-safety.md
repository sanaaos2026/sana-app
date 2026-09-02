---
name: Sana database test safety
description: Prevent tests from destroying an environment-bound PostgreSQL or Supabase schema.
---

Tests must never call destructive force-initialization against Sana's resolved database connection. Initialize non-destructively, create uniquely identified fixture rows, and delete only those rows during teardown.

**Why:** Sana resolves its database from environment secrets, so a test process may point at the real shared Supabase database rather than a disposable local database. Force initialization can therefore drop the shared schema.

**How to apply:** Use normal idempotent schema initialization only. Keep fixture identifiers unique and explicit, and clean them up in foreign-key order. A destructive reset requires a verified disposable database and explicit human approval.
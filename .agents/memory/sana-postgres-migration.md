---
name: Sana SQLite → PostgreSQL migration
description: Migration approach for moving Sana's Flask app off SQLite onto Postgres (Replit-managed, then Supabase), and lessons about connecting to Supabase Postgres from outside the Supabase MCP.
---

- Compatibility-wrapper pattern (auto `?`→`%s`, DictCursor, information_schema instead of PRAGMA) avoided rewriting every call site during the original SQLite→Postgres move.

## Connecting an external app (Railway, etc.) to a Supabase Postgres project

- Supabase's direct host (`db.<ref>.supabase.co:5432`) requires IPv6 egress; from Replit's shell this fails with an empty/opaque `OperationalError`. Use the **connection pooler** instead: host `aws-0-<region>.pooler.supabase.com`, user `postgres.<project_ref>` (not just `postgres`), dbname `postgres`. Port `5432` = session pooler (works well for a long-running server like Flask/Railway); port `6543` = transaction pooler (serverless/short-lived connections).
- **Why:** direct connections are IPv6-only on most Supabase regions; the pooler is IPv4-reachable and is also what Supabase recommends for external platforms.
- The actual Postgres role password (needed for `psycopg2`/`DATABASE_URL`) is **only** on the Database Settings page (`/dashboard/project/<ref>/database/settings`, note: not `/settings/database`), under "Database password" → "Reset database password". It is NOT the anon/service_role API key, NOT the `sbp_...` personal access token, and NOT the REST project URL — a non-technical user asked for "the database password" will often paste one of those three by mistake since they all live in nearby-sounding Supabase dashboard screens. If a supplied password fails auth, ask them to paste the **full `postgresql://...` URI** shown after resetting, rather than asking them to isolate one field themselves, and parse it programmatically.

## Runtime database precedence

Supabase is Sana's canonical runtime database; do not silently move production back to a stale Replit/Neon database.

**Why:** Replit's runtime-managed `DATABASE_URL` can remain attached to stale Neon credentials and fail password authentication even while the canonical Supabase database and its data are healthy.

**How to apply:** Preserve Supabase as the source of truth when changing hosting, deployment, or database configuration.

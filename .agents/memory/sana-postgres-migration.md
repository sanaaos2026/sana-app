---
name: Sana SQLite → PostgreSQL migration
description: How Sana's Flask backend was ported from sqlite3 to Postgres, and the one-off migration script pattern used.
---

Sana's schema used only `TEXT`/`INTEGER`/`REAL` columns with app-generated TEXT primary keys (no `AUTOINCREMENT`), so the SQLite→Postgres schema port needed almost no type changes — the only real incompatibility was `datetime('now')` column defaults, replaced with `to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')` to keep the exact same stored string format the app already parses elsewhere.

**Why:** the app has dozens of `db.execute(sql, (?, ?))` call sites across routes and one-off seed scripts; rewriting every call site to `%s`/psycopg2 idioms would have been high-risk for a codebase with no test suite.

**How to apply:** instead of touching call sites, add a thin compatibility wrapper class around a psycopg2 connection that: (1) auto-replaces `?` with `%s` in `execute()`/`executemany()`, (2) uses `psycopg2.extras.DictCursor` (not `RealDictCursor`) so rows support both `row["col"]` and `row[0]` — matching `sqlite3.Row` behavior exactly, since some code did `cur.fetchone()[0]` for `COUNT(*)` checks. `PRAGMA table_info(table)` calls (SQLite's way of checking existing columns for idempotent migrations) become `SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=?`.

For the one-time data move itself: a standalone script connects to both databases, deletes/truncates Postgres tables in reverse FK order, then bulk-copies every row from SQLite (via `sqlite3.Row.keys()` to get exact column lists) into Postgres in forward FK order, and verifies row counts match per table before declaring success. This preserves original timestamps/IDs exactly rather than regenerating data via seed scripts (which only had the original demo values, not real customer signups/mutations).

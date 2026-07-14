---
name: Sana uploaded zip drift (stale full-file baselines)
description: User-uploaded zips for new Sana features often bundle a stale app.py/schema.sql alongside the actually-new file(s); do not overwrite current files with them.
---

The user periodically uploads a zip containing the *entire* `sana_full/` tree plus one or two genuinely new files (a new seed script, a new template). The bundled `app.py`/`schema.sql` inside the zip are frequently forked from an older point in project history — missing the Postgres migration, missing later columns/tables, missing auth — even though the user describes them as "updated".

**Why:** blindly copying the zip's `app.py`/`schema.sql` over the current ones would silently revert the SQLite→Postgres migration, drop columns added by later features, and reintroduce the pre-auth cross-tenant `?company_id=` bypass. The user has no visibility into this drift; they just re-zip whatever local copy they're iterating on.

**How to apply:**
- Before applying any uploaded `app.py`/`schema.sql`, `diff` them against the current project versions.
- If the zip version is older/missing unrelated features, do NOT overwrite — instead extract only the genuinely new pieces (new tables, new routes, new template) and hand-merge them additively into the current files, preserving every existing column/table/route.
- Files that are self-contained and don't touch existing schema/routes (new seed scripts, new templates) can usually be copied in directly, but still check for SQLite-isms (e.g. `datetime('now')`) that need the Postgres-safe equivalent used elsewhere in the codebase.

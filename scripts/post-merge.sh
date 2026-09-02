#!/usr/bin/env bash
set -euo pipefail

# This workspace contains Sana's Flask/PostgreSQL application and a separate
# template @workspace/db package whose Drizzle schema is intentionally empty.
# Do not run `drizzle-kit push` here: it introspects DATABASE_URL, which may
# point at an unavailable legacy database, and an empty schema must never be
# allowed to mutate Sana's tables after a merge.
pnpm install --frozen-lockfile
pnpm run typecheck
pnpm --filter @workspace/api-server run build

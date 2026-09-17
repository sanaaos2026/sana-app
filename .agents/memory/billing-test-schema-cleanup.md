---
name: Billing test schema cleanup
description: Safety boundary for removing abandoned PostgreSQL schemas created by Sana billing tests
---

Automatically delete a billing-test schema only when its Sana-owned name encodes a verifiable creation time, it is older than the retention threshold, and no Sana session is using it. Never infer age from a PostgreSQL namespace identifier or from an untracked legacy name.

**Why:** PostgreSQL does not expose schema creation timestamps. Prefix-only legacy names therefore prove ownership intent but not age, and deleting them automatically would violate the requirement to preserve active or unrelated schemas.

**How to apply:** Keep future disposable schema names timestamped, mark test connections so activity can be detected, disable cleanup in production processes, and route any untracked historical names through an explicit inventory and review instead of automatic deletion.
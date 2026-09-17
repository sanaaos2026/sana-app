---
name: Sana cleanup freshness alerts
description: How scheduled-worker freshness warnings avoid duplicate admin notifications.
---

Treat a completed cleanup and a lock-contention skip as reliable proof that the daily billing-cleanup schedule was reached. Derive the dashboard warning from the newest reliable result instead of persisting a new alert during a dashboard read.

**Why:** Page views are not operational events. Writing to an email or notification outbox while rendering admin state would resend the same stale warning whenever the dashboard is reopened and would require a separate recovery process to clear it.

**How to apply:** For scheduled-worker health shown in Sana administration, compute stale/healthy state from durable worker results. Only a scheduler or dedicated delivery job may create proactive notifications, with its own deduplication and recovery semantics.
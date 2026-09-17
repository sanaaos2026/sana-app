---
name: Stripe connector runtime boundary
description: How Sana should distinguish agent-side Stripe connector access from Flask runtime access.
---

Treat Stripe connector access in the agent sandbox and Stripe access from the
running Flask application as two separate runtime capabilities. A successful
agent-side `proxyFetch` does not prove that an arbitrary shell process has the
Replit identity variables needed to fetch connection settings.

**Why:** The connected Stripe account accepted authenticated API writes through
the connector proxy, while a standalone Python shell process could not obtain
the connection settings because it lacked runtime identity. The Python
`replit-connectors` package was also unavailable from the workspace package
registry.

**How to apply:** Use the connection object's `proxyFetch` for agent-run Stripe
operations. In application code, use Replit's documented runtime connection
settings flow and fail explicitly when runtime identity is unavailable. Never
copy Stripe secrets into code, logs, or chat.
---
name: Stripe runtime setting aliases
description: Compatibility rule for Stripe credentials returned by Replit's runtime connection endpoint.
---

The Stripe runtime connection payload may expose the private API credential as
`secret`, while older application code may expect `secret_key`. Normalize both
names at the connection boundary and use one internal name afterward.

**Why:** Agent-side Stripe proxy calls worked, but the Flask runtime reported
the connection unavailable because it rejected the current field name even
though the credential was present.

**How to apply:** Accept both aliases without logging either value. Treat the
absence of both as an explicit unavailable-connection error.
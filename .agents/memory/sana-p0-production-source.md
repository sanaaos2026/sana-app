---
name: Sana P0 production source
description: Defines the temporary source-of-truth boundary for the P0 launch runtime and mirrors.
---

For the P0 launch phase, the current Replit workspace is the temporary development release source, `sanaclarity.com` on Replit Production is the only production runtime, and the current Supabase project is the database. GitHub is backup/mirror only and must not block publishing; Railway must not run in parallel.

**Why:** Standard GitHub push authentication failed, and the user explicitly chose a Replit-first fallback rather than force pushes, API blob uploads, sync workarounds, or parallel production paths.

**How to apply:** Publish P0 only through Replit Production, verify the custom domain and runtime there, and keep GitHub/Railway failures non-blocking unless the user explicitly changes this source-of-truth decision.
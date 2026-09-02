---
name: Long-page browser screenshots
description: Stability rule for browser suites that capture several long Sana pages.
---

Run each long, full-page screenshot case in a separate browser process rather than reusing one Chromium process for the entire matrix.

**Why:** Reusing one Chromium process across several long passport/report captures repeatedly stalled the final case even though its API and template rendered normally in isolation. Fresh pages alone did not release enough browser resources.

**How to apply:** When a browser suite captures multiple full-page Sana reports, keep data fixtures isolated and launch/close Chromium per case. Wait for a meaningful page selector instead of `networkidle`, because external fonts can keep network-idle checks unstable.
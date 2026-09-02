---
name: Sana browser diagnostics
description: Safe retention rules for Playwright failure evidence in authenticated browser tests.
---

Playwright tracing for authenticated tests should begin only after login succeeds; retain screenshot and trace files only when the test fails, with random run-only names.

**Why:** Starting the trace around the login flow can capture password input or other authentication material, while keeping successful artifacts creates local noise and obscures the failure that needs investigation.

**How to apply:** Save failure evidence under a per-run directory, print both paths to the test log, stop tracing without a path on success, and remove any successful-run directory.
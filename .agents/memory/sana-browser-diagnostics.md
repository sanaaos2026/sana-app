---
name: Sana browser diagnostics
description: Safe retention rules for Playwright failure evidence in authenticated browser tests.
---

Playwright tracing for authenticated tests should begin only after login succeeds; retain screenshot and trace files only when the test fails, with random run-only names. Workflow failures upload both to a private Google Drive evidence folder, with the trace sanitized first.

**Why:** Starting the trace around the login flow can capture password input or other authentication material, while keeping successful artifacts creates local noise and obscures the failure that needs investigation. The runner is temporary, so failed evidence needs durable retention; trace archives can also contain authenticated headers or cookies.

**How to apply:** Save failure evidence under a per-run directory, print both paths to the test log, stop tracing without a path on success, and remove any successful-run directory. Enable the Google Drive upload only in the browser workflow; redact credential-like trace fields and known test secrets before upload, and print the remote folder/file links without logging credentials.
---
name: Sana digital source boundaries
description: How company website and social links may be used honestly in Sana diagnostics.
---

Company website, social-media, and business-reference URLs may be stored with the company profile and passed into the diagnosis as contextual references.

**Why:** A plain URL does not give the Anthropic messages call browsing access. Treating the URL as inspected content would create unsupported findings and undermine the evidence model.

**How to apply:** Until a safe content-retrieval and evidence-capture step exists, the diagnostic prompt must say that links were supplied by the client but not opened. Use only the existence and type of channel as context; request captured content or further evidence for claims about what the page says.

Google Drive folder presence or a `000-اقرأ أولاً` placeholder is not evidence that the folder's contents were reviewed; verify actual file content separately.

**Why:** The Sana OS Drive tree currently contains mostly empty placeholder Google Docs, so metadata-only inspection can falsely suggest that the knowledge base is populated.

**How to apply:** Use Drive as a controlled intake/catalog source, keep private material in an explicit review queue, and never make production diagnostics depend on Replit's Drive connector or claim a file was read without captured content.
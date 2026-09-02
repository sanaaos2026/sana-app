---
name: Workspace Python publish builds
description: Why the root pyproject must explicitly disable setuptools discovery in this mixed workspace.
---

Treat the repository root as a dependency manifest, not an installable Python package, and explicitly disable both package and module discovery.

**Why:** Replit's publish builder can build the root `pyproject.toml` before running artifact-specific build commands. Setuptools may interpret unrelated workspace directories as namespace packages and fail on multiple top-level packages.

**How to apply:** When adding root-level directories or changing Python packaging metadata, verify that a clean `pip wheel . --no-deps` succeeds as part of publish troubleshooting.
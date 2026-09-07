---
name: Uptodown availability
description: External Uptodown catalog behavior observed while integrating the dashboard store source
---

Uptodown's public catalog and localized app domains returned HTTP 410 during testing on September 7, 2026. The dashboard should treat this as an upstream availability error, not fabricate search results or silently substitute another source.

**Why:** The store flow depends on Uptodown exposing searchable app pages and direct APK links; when the upstream catalog is unavailable, an apparently successful selection would produce an unusable build.

**How to apply:** Preserve the explicit error state and the parser fixtures. Recheck the current Uptodown endpoint patterns before changing the adapter or adding a fallback source.
---
name: Uptodown availability
description: External Uptodown catalog behavior observed while integrating the dashboard store source
---

Uptodown's public catalog and localized app domains returned HTTP 410 during testing on September 7, 2026. App pages can be read through Google Translate, but current downloads require an interactive Cloudflare Turnstile challenge and do not expose a server-side APK URL.

**Why:** The store flow depends on Uptodown exposing searchable app pages and direct APK links; when the upstream catalog or download challenge blocks the server, an apparently successful selection would produce an unusable build.

**How to apply:** Use the translated page fallback and web-indexed app-page fallback only for metadata; preserve explicit browser-required/download errors and never treat an HTML download page as an APK.
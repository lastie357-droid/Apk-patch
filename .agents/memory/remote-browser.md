---
name: Chromium remote downloads
description: Environment-specific details for the server-side Chromium session used by the dashboard
---

Chromium exposes a browser-level DevTools socket from `/json/version`, but
page commands such as `Page.enable` and `Input.dispatchMouseEvent` must use the
WebSocket URL for a `type: page` target from `/json/list`. Downloaded files
should be processed only after their size remains unchanged across a debounce
interval; Chromium may briefly expose a partially written `.apk` before the
download is complete.

**Why:** Using the browser socket makes page commands fail with “Page.enable
wasn't found”, and processing on first appearance can copy a partial APK.

**How to apply:** Select a page target when opening a session and validate the
APK signature after a stable-size check before adding it to the saved-app index.
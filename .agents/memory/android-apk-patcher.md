---
name: Android APK patcher bootstrap
description: Durable environment notes for the repository's self-bootstrapping APK rebuild script.
---

The Android command-line tools' sdkmanager and build-tools launcher scripts
require the selected JDK's `bin` directory to be on `PATH`, not only
`JAVA_HOME`. When accepting SDK licenses with `yes` under Bash `pipefail`,
status 141 from `yes` is expected after sdkmanager closes stdin; a nonzero
status other than 0 or 141 indicates an actual SDK installation failure.

**Why:** This environment starts without Java or Android SDK binaries, so the
script must bootstrap them and still work under strict Bash error handling.

**How to apply:** Preserve both `JAVA_HOME` and `PATH` setup before invoking
sdkmanager, apksigner, or zipalign, and handle the license pipeline status
explicitly.
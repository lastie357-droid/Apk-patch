# APK external-intent patcher

Intent Forge includes a dashboard and a CLI. Both decompile an APK with
Apktool, change one launchable component, rebuild it, zip-align it, and sign
the result with a temporary generated key.

The default manifest change is:

```xml
<activity
    android:exported="true"
    android:launchMode="singleTask" />
```

This makes the selected activity callable by another app and causes Android to
reuse its existing task/activity when possible. A new intent is delivered to
the existing activity's `onNewIntent()` callback. It does **not** make arbitrary
internal app code callable, and it does not preserve the original APK
signature. Only use this with APKs you are authorized to modify.

## Dashboard

The Replit workflow starts the dashboard automatically on port 5000. It
provides:

- Uptodown app search with selectable app cards and server-side APK link
  resolution when Uptodown exposes a direct file.
- A server-side Chromium browser for interactive Uptodown downloads. The
  dashboard shows the browser view, forwards clicks to it, and saves a
  completed APK under `.dashboard/saved-apps/`.
- A saved-app selector so an APK downloaded once on the server can be reused
  for later builds without downloading or uploading it again.
- Local APK upload or a public direct APK URL.
- Automatic `MAIN` / `LAUNCHER` activity selection, plus an optional component
  field for choosing a specific activity or alias.
- `singleTask`, `singleTop`, `standard`, and `singleInstance` choices.
- Generated debug or test signing keys.
- Live build logs and a download button when the patched APK is ready.

Builds run one at a time and APK files remain in the workspace. Selecting an
Uptodown result opens a server-side Chromium session. Complete any
Cloudflare/Turnstile check and click the download control in the embedded
browser view; the resulting APK is detected and copied into the server's
saved-app directory. Select it from **Saved on server** to invoke the same
`build.sh` pipeline without a device download or re-upload. Direct APK URLs
remain supported. The server does not accept keystores or signing passwords.
Uptodown metadata can be loaded through a translated proxy when the direct
catalog returns HTTP 410.

## CLI quick start

```bash
./build.sh --fetch-test-apk --output ./out/termux.external.apk
```

The test mode downloads the pinned `com.termux_118.apk` from F-Droid and
verifies its SHA-256 before using it. The APK is cached in
`.cache/test-apk/`; generated tools and working files are also ignored by Git.

For your own APK:

```bash
./build.sh ./input.apk --output ./out/input.external.apk
```

If an APK has multiple launchable activities, select one explicitly:

```bash
./build.sh ./input.apk \
  --component com.example.app.MainActivity \
  --launch-mode singleTask \
  --signing-mode test \
  --output ./out/input.external.apk
```

Supported launch modes are `standard`, `singleTop`, `singleTask`, and
`singleInstance`. `singleTask` is the default because it reuses the existing
task instead of creating another activity instance. If the existing activity
is not at the top of its task, Android may finish activities above it as part
of normal `singleTask` behavior. `--signing-mode` accepts `debug` or `test`;
both keys are generated temporarily for testing.

## Calling the patched APK

The caller should use an explicit component intent:

```java
Intent intent = new Intent();
intent.setComponent(new ComponentName(
    "com.example.app",
    "com.example.app.MainActivity"
));
intent.addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP
        | Intent.FLAG_ACTIVITY_SINGLE_TOP);
startActivity(intent);
```

The caller needs whatever normal Android permission or package-visibility rules
apply to its own use case. The patched APK is signed with a new key, so it
cannot be installed as an update over the original signed APK. To update the
original app, use the downloaded APK as an input to the Android SDK's
`zipalign` and `apksigner` tools locally with the original publisher key.
Intent Forge never receives that key:

```bash
zipalign -p -f 4 patched.apk aligned.apk
apksigner sign --ks /path/to/original-key.jks aligned.apk
apksigner verify aligned.apk
```

## Bootstrapped tools

The script downloads and caches:

- Temurin JDK 17 if Java is not already available.
- Apktool `2.10.0`.
- Android command-line tools and build-tools `35.0.0`, including `zipalign`
  and `apksigner`.

Override the cache locations or tool versions with the environment variables
shown by `./build.sh --help`. No system package manager or preinstalled Android
SDK is required.
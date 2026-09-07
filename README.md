# APK external-intent patcher

`build.sh` decompiles an APK with Apktool, changes one launchable component,
rebuilds it, zip-aligns it, and signs the result with a temporary debug key.

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

## Quick start

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
  --output ./out/input.external.apk
```

Supported launch modes are `standard`, `singleTop`, `singleTask`, and
`singleInstance`. `singleTask` is the default because it reuses the existing
task instead of creating another activity instance. If the existing activity
is not at the top of its task, Android may finish activities above it as part
of normal `singleTask` behavior.

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
apply to its own use case. The patched APK is signed with a new debug key, so
it cannot be installed as an update over the original signed APK.

## Bootstrapped tools

The script downloads and caches:

- Temurin JDK 17 if Java is not already available.
- Apktool `2.10.0`.
- Android command-line tools and build-tools `35.0.0`, including `zipalign`
  and `apksigner`.

Override the cache locations or tool versions with the environment variables
shown by `./build.sh --help`. No system package manager or preinstalled Android
SDK is required.
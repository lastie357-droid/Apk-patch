#!/usr/bin/env bash
#
# Patch an Android APK so its launchable activity can be invoked by another
# application without creating a second activity instance.
#
# Usage:
#   ./build.sh input.apk
#   ./build.sh input.apk --component com.example.MainActivity
#   ./build.sh --fetch-test-apk
#
# The default patch is:
#   android:exported="true"
#   android:launchMode="singleTask"
#
# The caller should send an explicit intent to the patched component. Android
# then reuses the existing task/activity and delivers a new intent through
# onNewIntent(), rather than creating another instance.

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TOOLS_DIR="${APK_INTENT_TOOLS_DIR:-$SCRIPT_DIR/.android-tools}"
WORK_ROOT="${APK_INTENT_WORK_DIR:-$SCRIPT_DIR/.apk-work}"
TEST_APK_DIR="${APK_INTENT_TEST_DIR:-$SCRIPT_DIR/.cache/test-apk}"
APKTOOL_VERSION="${APKTOOL_VERSION:-2.10.0}"
BUILD_TOOLS_VERSION="${BUILD_TOOLS_VERSION:-35.0.0}"
JDK_VERSION="${JDK_VERSION:-17.0.12_7}"
APKTOOL_JAR="$TOOLS_DIR/apktool_${APKTOOL_VERSION}.jar"

APKTOOL_URL="https://github.com/iBotPeaches/Apktool/releases/download/v${APKTOOL_VERSION}/apktool_${APKTOOL_VERSION}.jar"
ANDROID_CMDLINE_TOOLS_URL="${ANDROID_CMDLINE_TOOLS_URL:-https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip}"
JDK_URL="${JDK_URL:-https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.12%2B7/OpenJDK17U-jdk_x64_linux_hotspot_17.0.12_7.tar.gz}"
TEST_APK_URL="https://f-droid.org/repo/com.termux_118.apk"
TEST_APK_SHA256="822ac152bd7c2d9770b87c1feea03f22f2349a91b94481b268c739493a260f0b"

ANDROID_SDK_ROOT="$TOOLS_DIR/android-sdk"
export ANDROID_SDK_ROOT

KEEP_WORKDIR=0
COMPONENT=""
LAUNCH_MODE="singleTask"
SIGNING_MODE="debug"
OUTPUT=""
INPUT=""
FETCH_TEST_APK=0

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

info() {
  printf '[build] %s\n' "$*" >&2
}

usage() {
  cat <<'EOF'
Usage:
  build.sh INPUT.apk [options]
  build.sh --fetch-test-apk [options]

Options:
  --output PATH             Output signed APK path.
  --component NAME          Activity or activity-alias to patch. If omitted,
                            the MAIN/LAUNCHER component is selected.
  --launch-mode MODE        standard, singleTop, singleTask, or singleInstance.
                            Default: singleTask.
  --signing-mode MODE       debug or test. Both use a generated local key.
                            Default: debug.
  --fetch-test-apk          Download the pinned F-Droid Termux APK and use it.
  --keep-workdir            Keep the decoded/rebuilt working directory.
  --help                    Show this help.

Environment:
  APK_INTENT_TOOLS_DIR      Tool cache directory.
  APK_INTENT_WORK_DIR       Temporary build directory parent.
  APK_INTENT_TEST_DIR       Test APK cache directory.
  BUILD_TOOLS_VERSION       Android build-tools version, default 35.0.0.

The result is signed with a locally generated debug keystore whose password is
only used inside this script. It is suitable for testing, not Play Store
publishing. A repackaged APK cannot be updated over the original signed APK.
EOF
}

need_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command '$1' is missing"
}

download() {
  local url="$1"
  local destination="$2"
  mkdir -p "$(dirname -- "$destination")"
  info "downloading $(basename -- "$destination")"
  curl --fail --location --retry 3 --retry-delay 2 --silent --show-error \
    "$url" -o "$destination"
}

find_java() {
  if [[ -n "${JAVA_HOME:-}" && -x "${JAVA_HOME}/bin/java" ]]; then
    printf '%s\n' "${JAVA_HOME}/bin/java"
    return
  fi
  if command -v java >/dev/null 2>&1; then
    command -v java
    return
  fi
  local java_binary
  java_binary="$(find "$TOOLS_DIR/jdk" -type f -path '*/bin/java' -executable -print -quit 2>/dev/null || true)"
  [[ -n "$java_binary" ]] || return 1
  printf '%s\n' "$java_binary"
}

ensure_java() {
  local java_binary
  java_binary="$(find_java || true)"
  if [[ -n "$java_binary" ]]; then
    JAVA_BIN="$java_binary"
    export JAVA_HOME="$(cd -- "$(dirname -- "$JAVA_BIN")/.." && pwd)"
    export PATH="$(dirname -- "$JAVA_BIN"):$PATH"
    return
  fi

  need_command curl
  need_command tar
  mkdir -p "$TOOLS_DIR/jdk"
  local archive="$TOOLS_DIR/jdk-temurin17.tar.gz"
  [[ -f "$archive" ]] || download "$JDK_URL" "$archive"
  if ! find_java >/dev/null 2>&1; then
    info "extracting Temurin JDK 17"
    tar -xzf "$archive" -C "$TOOLS_DIR/jdk"
  fi
  JAVA_BIN="$(find_java || true)"
  [[ -n "$JAVA_BIN" ]] || die "could not find Java after installing the bootstrap JDK"
  export JAVA_HOME="$(cd -- "$(dirname -- "$JAVA_BIN")/.." && pwd)"
  export PATH="$(dirname -- "$JAVA_BIN"):$PATH"
}

ensure_apktool() {
  if [[ ! -s "$APKTOOL_JAR" ]]; then
    need_command curl
    download "$APKTOOL_URL" "$APKTOOL_JAR"
  fi
}

ensure_android_build_tools() {
  local sdkmanager="$ANDROID_SDK_ROOT/cmdline-tools/latest/bin/sdkmanager"
  if [[ ! -x "$sdkmanager" ]]; then
    need_command curl
    need_command unzip
    local archive="$TOOLS_DIR/commandlinetools-linux.zip"
    [[ -f "$archive" ]] || download "$ANDROID_CMDLINE_TOOLS_URL" "$archive"
    mkdir -p "$ANDROID_SDK_ROOT/cmdline-tools"
    local unpack_dir
    unpack_dir="$(mktemp -d "${TMPDIR:-/tmp}/android-cmdline-tools.XXXXXX")"
    unzip -q "$archive" -d "$unpack_dir"
    rm -rf "$ANDROID_SDK_ROOT/cmdline-tools/latest"
    mv "$unpack_dir/cmdline-tools" "$ANDROID_SDK_ROOT/cmdline-tools/latest"
    rm -rf "$unpack_dir"
  fi

  local build_tools_dir="$ANDROID_SDK_ROOT/build-tools/$BUILD_TOOLS_VERSION"
  if [[ ! -x "$build_tools_dir/apksigner" || ! -x "$build_tools_dir/zipalign" ]]; then
    info "installing Android build-tools $BUILD_TOOLS_VERSION"
    set +e
    yes | "$ANDROID_SDK_ROOT/cmdline-tools/latest/bin/sdkmanager" \
      --sdk_root="$ANDROID_SDK_ROOT" \
      "platform-tools" "build-tools;$BUILD_TOOLS_VERSION" >/dev/null
    local sdkmanager_status=$?
    set -e
    # With pipefail enabled, yes commonly returns 141 after sdkmanager closes
    # its input. That is successful license installation, not an SDK failure.
    [[ "$sdkmanager_status" -eq 0 || "$sdkmanager_status" -eq 141 ]] || \
      die "sdkmanager failed with exit code $sdkmanager_status"
  fi

  APKSIGNER="$build_tools_dir/apksigner"
  ZIPALIGN="$build_tools_dir/zipalign"
  [[ -x "$APKSIGNER" ]] || die "apksigner was not installed"
  [[ -x "$ZIPALIGN" ]] || die "zipalign was not installed"
}

fetch_test_apk() {
  mkdir -p "$TEST_APK_DIR"
  local test_apk="$TEST_APK_DIR/termux_118.apk"
  if [[ ! -s "$test_apk" ]]; then
    need_command curl
    download "$TEST_APK_URL" "$test_apk"
  fi
  local actual
  actual="$(sha256sum "$test_apk" | awk '{print $1}')"
  [[ "$actual" == "$TEST_APK_SHA256" ]] || {
    rm -f "$test_apk"
    die "test APK checksum mismatch; expected $TEST_APK_SHA256, got $actual"
  }
  printf '%s\n' "$test_apk"
}

patch_manifest() {
  local manifest="$1"
  local requested_component="$2"
  local launch_mode="$3"
  "$PYTHON_BIN" - "$manifest" "$requested_component" "$launch_mode" <<'PY'
import sys
import xml.etree.ElementTree as ET

manifest_path, requested, launch_mode = sys.argv[1:4]
ANDROID = "http://schemas.android.com/apk/res/android"
NAME = f"{{{ANDROID}}}name"
EXPORTED = f"{{{ANDROID}}}exported"
LAUNCH_MODE = f"{{{ANDROID}}}launchMode"
TARGET_ACTIVITY = f"{{{ANDROID}}}targetActivity"

tree = ET.parse(manifest_path)
root = tree.getroot()
package_name = root.get("package")
if not package_name:
    raise SystemExit("decoded manifest has no package attribute")

application = root.find("application")
if application is None:
    raise SystemExit("manifest has no application element")

def full_name(value):
    if value.startswith("."):
        return package_name + value
    if "." not in value:
        return package_name + "." + value
    return value

def is_launcher(component):
    for intent_filter in component.findall("intent-filter"):
        actions = {
            action.get(NAME)
            for action in intent_filter.findall("action")
        }
        categories = {
            category.get(NAME)
            for category in intent_filter.findall("category")
        }
        if "android.intent.action.MAIN" in actions and (
            "android.intent.category.LAUNCHER" in categories
            or "android.intent.category.LEANBACK_LAUNCHER" in categories
        ):
            return True
    return False

components = [
    element
    for element in list(application)
    if element.tag in ("activity", "activity-alias") and element.get(NAME)
]

selected = None
if requested:
    requested_full = full_name(requested)
    for component in components:
        if full_name(component.get(NAME)) == requested_full:
            selected = component
            break
    if selected is None:
        available = ", ".join(full_name(c.get(NAME)) for c in components)
        raise SystemExit(
            f"component {requested_full} was not found; available activities: {available}"
        )
else:
    launchers = [component for component in components if is_launcher(component)]
    if not launchers:
        raise SystemExit(
            "no MAIN/LAUNCHER activity or activity-alias found; use --component"
        )
    selected = launchers[0]

selected_name = full_name(selected.get(NAME))
patched = [selected]

# An activity-alias is the externally visible launcher in many APKs. Its
# exported flag belongs on the alias, while launchMode belongs on the target
# activity. Keep both correct when an alias is selected.
if selected.tag == "activity-alias":
    target_name = selected.get(TARGET_ACTIVITY)
    if target_name:
        target_full = full_name(target_name)
        for component in components:
            if component.tag == "activity" and full_name(component.get(NAME)) == target_full:
                patched.append(component)
                break

for component in patched:
    component.set(EXPORTED, "true")

launch_target = patched[-1]
launch_target.set(LAUNCH_MODE, launch_mode)

ET.register_namespace("android", ANDROID)
tree.write(manifest_path, encoding="utf-8", xml_declaration=True)
print(f"patched {selected_name} (exported=true, launchMode={launch_mode})")
PY
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --output)
        [[ $# -ge 2 ]] || die "--output requires a path"
        OUTPUT="$2"
        shift 2
        ;;
      --component)
        [[ $# -ge 2 ]] || die "--component requires an activity name"
        COMPONENT="$2"
        shift 2
        ;;
      --launch-mode)
        [[ $# -ge 2 ]] || die "--launch-mode requires a value"
        LAUNCH_MODE="$2"
        shift 2
        ;;
      --signing-mode)
        [[ $# -ge 2 ]] || die "--signing-mode requires a value"
        SIGNING_MODE="$2"
        shift 2
        ;;
      --fetch-test-apk)
        FETCH_TEST_APK=1
        shift
        ;;
      --keep-workdir)
        KEEP_WORKDIR=1
        shift
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      -*)
        die "unknown option: $1 (use --help)"
        ;;
      *)
        [[ -z "$INPUT" ]] || die "only one input APK is supported"
        INPUT="$1"
        shift
        ;;
    esac
  done

  [[ "$LAUNCH_MODE" =~ ^(standard|singleTop|singleTask|singleInstance)$ ]] || \
    die "invalid --launch-mode '$LAUNCH_MODE'"
  [[ "$SIGNING_MODE" =~ ^(debug|test)$ ]] || \
    die "invalid --signing-mode '$SIGNING_MODE' (use debug or test)"
  [[ "$FETCH_TEST_APK" == 0 || -z "$INPUT" ]] || \
    die "do not provide an input APK together with --fetch-test-apk"
  if [[ "$FETCH_TEST_APK" == 1 ]]; then
    INPUT="$(fetch_test_apk)"
  fi
  [[ -n "$INPUT" ]] || {
    usage >&2
    exit 2
  }
  [[ -f "$INPUT" ]] || die "input APK not found: $INPUT"
  [[ "${INPUT##*.}" == "apk" ]] || die "input must have an .apk extension"

  if [[ -z "$OUTPUT" ]]; then
    local input_base
    input_base="${INPUT%.apk}"
    OUTPUT="${input_base}.external.apk"
  fi
  if [[ "$OUTPUT" != /* ]]; then
    OUTPUT="$PWD/$OUTPUT"
  fi
}

main() {
  need_command awk
  need_command find
  need_command mktemp
  need_command python3
  need_command sha256sum
  need_command tar
  need_command unzip
  need_command curl
  PYTHON_BIN="$(command -v python3)"

  parse_args "$@"
  mkdir -p "$TOOLS_DIR" "$WORK_ROOT" "$(dirname -- "$OUTPUT")"

  ensure_java
  ensure_apktool
  ensure_android_build_tools

  local workdir
  workdir="$(mktemp -d "$WORK_ROOT/apk-intent-patch.XXXXXX")"
  WORKDIR_TO_CLEAN="$workdir"
  local decoded="$workdir/decoded"
  local unsigned="$workdir/rebuilt-unsigned.apk"
  local aligned="$workdir/rebuilt-aligned.apk"
  local keystore="$workdir/${SIGNING_MODE}.keystore"
  local signing_alias
  local signing_name
  if [[ "$SIGNING_MODE" == "test" ]]; then
    signing_alias="apkintenttest"
    signing_name="APK Intent Test"
  else
    signing_alias="androiddebugkey"
    signing_name="Android Debug"
  fi
  local keytool_bin
  if [[ -x "${JAVA_HOME}/bin/keytool" ]]; then
    keytool_bin="${JAVA_HOME}/bin/keytool"
  else
    keytool_bin="$(command -v keytool || true)"
  fi
  [[ -n "$keytool_bin" && -x "$keytool_bin" ]] || die "could not find keytool"

  cleanup() {
    [[ -n "${WORKDIR_TO_CLEAN:-}" ]] || return 0
    if [[ "$KEEP_WORKDIR" == 0 ]]; then
      rm -rf "$WORKDIR_TO_CLEAN"
    else
      info "kept work directory: $WORKDIR_TO_CLEAN"
    fi
  }
  trap cleanup EXIT

  info "decoding $(basename -- "$INPUT")"
  "$JAVA_BIN" -jar "$APKTOOL_JAR" d --force --output "$decoded" "$INPUT" >/dev/null
  [[ -f "$decoded/AndroidManifest.xml" ]] || die "apktool did not produce AndroidManifest.xml"

  info "patching AndroidManifest.xml"
  patch_manifest "$decoded/AndroidManifest.xml" "$COMPONENT" "$LAUNCH_MODE"

  info "rebuilding APK"
  "$JAVA_BIN" -jar "$APKTOOL_JAR" b "$decoded" --output "$unsigned" >/dev/null
  info "aligning APK"
  "$ZIPALIGN" -p -f 4 "$unsigned" "$aligned"

  info "creating $SIGNING_MODE signing key"
  "$keytool_bin" \
    -genkeypair -noprompt \
    -keystore "$keystore" \
    -storepass android -keypass android \
    -alias "$signing_alias" \
    -keyalg RSA -keysize 2048 -validity 10000 \
    -dname "CN=$signing_name,O=APK Intent Patcher,C=US" >/dev/null 2>&1

  info "signing APK"
  "$APKSIGNER" sign \
    --ks "$keystore" \
    --ks-pass pass:android \
    --key-pass pass:android \
    --out "$OUTPUT" \
    "$aligned"
  "$APKSIGNER" verify --verbose "$OUTPUT" >/dev/null
  info "created $OUTPUT"
  info "external callers should use an explicit component intent; see README.md"
}

main "$@"
#!/usr/bin/env bash
# Build the arm64 validation app. Android SDK 35, NDK 28.2 and CMake 3.22.1 are required.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
build_tools="$root/.tools/android-build"
mkdir -p "$build_tools" "$root/android/app/libs" "$root/dist/android-validation"
export ANDROID_HOME="${ANDROID_HOME:-$build_tools/sdk}"
export ANDROID_USER_HOME="$build_tools/android-user"
export GRADLE_USER_HOME="${GRADLE_USER_HOME:-$build_tools/gradle}"
if [[ -z "${JAVA_HOME:-}" ]]; then
    for candidate in "$build_tools"/jdk-*/Contents/Home; do
        if [[ -x "$candidate/bin/java" ]]; then export JAVA_HOME="$candidate"; break; fi
    done
fi
if [[ ! -x "${JAVA_HOME:-}/bin/java" || ! -f "$ANDROID_HOME/platforms/android-35/android.jar" ]]; then
    echo "Set JAVA_HOME to JDK 17+ and ANDROID_HOME to an SDK with platform 35, build-tools 35.0.0, NDK 28.2.13676358 and CMake 3.22.1." >&2
    exit 1
fi
archive="$root/android/app/libs/sherpa-onnx-2.0.1.aar"
if [[ ! -f "$archive" ]]; then
    curl --fail --location --retry 3 https://github.com/k2-fsa/sherpa-onnx/releases/download/v2.0.1/sherpa-onnx-2.0.1.aar --output "$archive"
fi
actual="$(shasum -a 256 "$archive" | cut -d ' ' -f 1)"
[[ "$actual" == a8d09d1d32f3eb8520482c6d3d43fae674681c5b050385967c70a3c2c9bf0ee7 ]] || { echo 'sherpa-onnx checksum mismatch' >&2; exit 1; }
if [[ ! -d "$build_tools/llama.cpp/.git" ]]; then
    git clone --depth 1 --branch b9000 https://github.com/ggml-org/llama.cpp.git "$build_tools/llama.cpp"
fi
[[ "$(git -C "$build_tools/llama.cpp" rev-parse HEAD)" == 1a03cf47f67be591699d1f0f7ca28e1ed6eb8c7e ]] || { echo 'llama.cpp revision mismatch' >&2; exit 1; }
if [[ ! -f "$build_tools/debug.keystore" ]]; then
    "$JAVA_HOME/bin/keytool" -genkeypair -keystore "$build_tools/debug.keystore" -storepass android -alias androiddebugkey -keypass android -dname 'CN=LectureFlow Android Validation' -keyalg RSA -keysize 2048 -validity 10000
fi
printf 'sdk.dir=%s\n' "$ANDROID_HOME" > "$root/android/local.properties"
cd "$root/android"
./gradlew assembleDebug assembleDebugAndroidTest testDebugUnitTest lintDebug
cp app/build/outputs/apk/debug/app-debug.apk "$root/dist/android-validation/LectureFlow-Android-0.1-validation-arm64.apk"
cd "$root/dist/android-validation"
shasum -a 256 LectureFlow-Android-0.1-validation-arm64.apk > SHA256SUMS

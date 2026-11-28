#!/bin/sh
# Standalone Mac audio tool from a separately verified upstream source checkout.
# Do not build against optional third-party libraries from Homebrew or the network.
set -eu
[ "$#" -eq 2 ] || { echo "usage: $0 /abs/ffmpeg-source /abs/out-dir" >&2; exit 2; }
source_dir=$(cd "$1" && pwd -P)
mkdir -p "$2"
output_dir=$(cd "$2" && pwd -P)
[ "$(uname -m)" = arm64 ] || { echo "Requires Apple Silicon" >&2; exit 2; }
build=$(mktemp -d "${TMPDIR:-/tmp}/lectureflow-ffmpeg.XXXXXX")
trap 'rm -rf "$build"' EXIT HUP INT TERM
export LC_ALL=C MACOSX_DEPLOYMENT_TARGET=26.0
cd "$build"
"$source_dir/configure" \
    --arch=aarch64 --cc=/usr/bin/clang --disable-autodetect --disable-network \
    --disable-doc --disable-ffplay --disable-ffprobe --disable-avdevice \
    --disable-shared --enable-static
/usr/bin/make -j "${FFMPEG_JOBS:-4}" ffmpeg
install -m 755 ffmpeg "$output_dir/ffmpeg"
"$output_dir/ffmpeg" -hide_banner -version > "$output_dir/version.txt"
"$output_dir/ffmpeg" -hide_banner -buildconf > "$output_dir/buildconf.txt" 2>&1
"$output_dir/ffmpeg" -hide_banner -L > "$output_dir/license.txt" 2>&1
/usr/bin/otool -L "$output_dir/ffmpeg" > "$output_dir/linked-libraries.txt"

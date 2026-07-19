#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
THIRDPART_DIR="$ROOT_DIR/3rdpart"
LICENSE_DIR="$THIRDPART_DIR/licenses"
WORK_DIR="${RUNNER_TEMP:-/tmp}/tube-player-linux-runtime"
DENO_VERSION="${DENO_VERSION:-v2.4.1}"

rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR" "$THIRDPART_DIR" "$LICENSE_DIR"

curl -fL --retry 5 --retry-delay 3 \
  "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_linux" \
  -o "$THIRDPART_DIR/yt-dlp_linux"
curl -fL --retry 5 --retry-delay 3 \
  "https://raw.githubusercontent.com/yt-dlp/yt-dlp/master/LICENSE" \
  -o "$LICENSE_DIR/yt-dlp-LICENSE"

curl -fL --retry 5 --retry-delay 3 \
  "https://github.com/denoland/deno/releases/download/$DENO_VERSION/deno-x86_64-unknown-linux-gnu.zip" \
  -o "$WORK_DIR/deno.zip"
unzip -q "$WORK_DIR/deno.zip" -d "$WORK_DIR/deno"
cp "$WORK_DIR/deno/deno" "$THIRDPART_DIR/deno"
curl -fL --retry 5 --retry-delay 3 \
  "https://raw.githubusercontent.com/denoland/deno/$DENO_VERSION/LICENSE.md" \
  -o "$LICENSE_DIR/deno-LICENSE.md"

curl -fL --retry 5 --retry-delay 3 \
  "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz" \
  -o "$WORK_DIR/ffmpeg.tar.xz"
mkdir -p "$WORK_DIR/ffmpeg"
tar -xJf "$WORK_DIR/ffmpeg.tar.xz" -C "$WORK_DIR/ffmpeg" --strip-components=1
cp "$WORK_DIR/ffmpeg/bin/ffmpeg" "$THIRDPART_DIR/ffmpeg"
cp "$WORK_DIR/ffmpeg/bin/ffprobe" "$THIRDPART_DIR/ffprobe"
curl -fL --retry 5 --retry-delay 3 \
  "https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/COPYING.GPLv3" \
  -o "$LICENSE_DIR/ffmpeg-COPYING.GPLv3"

LIBMPV_PATH="$(ldconfig -p | awk '/libmpv\.so/{print $NF; exit}')"
if [[ -z "$LIBMPV_PATH" || ! -f "$LIBMPV_PATH" ]]; then
  echo "libmpv was not found through ldconfig" >&2
  exit 1
fi
LIBMPV_REAL="$(readlink -f "$LIBMPV_PATH")"
LIBMPV_SONAME="$(objdump -p "$LIBMPV_REAL" | awk '/SONAME/{print $2; exit}')"
if [[ -z "$LIBMPV_SONAME" ]]; then
  LIBMPV_SONAME="$(basename "$LIBMPV_PATH")"
fi
cp "$LIBMPV_REAL" "$THIRDPART_DIR/$LIBMPV_SONAME"

LIBMPV_COPYRIGHT="$(find /usr/share/doc -maxdepth 2 -path '*/libmpv*/copyright' -print -quit)"
if [[ -n "$LIBMPV_COPYRIGHT" ]]; then
  cp "$LIBMPV_COPYRIGHT" "$LICENSE_DIR/libmpv-copyright"
else
  curl -fL --retry 5 --retry-delay 3 \
    "https://raw.githubusercontent.com/mpv-player/mpv/master/Copyright" \
    -o "$LICENSE_DIR/libmpv-Copyright"
fi

chmod 0755 "$THIRDPART_DIR/deno" "$THIRDPART_DIR/ffmpeg" \
  "$THIRDPART_DIR/ffprobe" "$THIRDPART_DIR/yt-dlp_linux"

"$THIRDPART_DIR/deno" --version > "$LICENSE_DIR/deno-version.txt"
"$THIRDPART_DIR/yt-dlp_linux" --version > "$LICENSE_DIR/yt-dlp-version.txt"
"$THIRDPART_DIR/ffmpeg" -hide_banner -version > "$LICENSE_DIR/ffmpeg-build-configuration.txt"
dpkg-query -W -f='${Package} ${Version}\n' 'libmpv*' > "$LICENSE_DIR/libmpv-package-version.txt" 2>/dev/null || true
printf '%s\n' \
  "Deno: https://github.com/denoland/deno/releases/tag/$DENO_VERSION" \
  "yt-dlp: https://github.com/yt-dlp/yt-dlp/releases/latest" \
  "FFmpeg binary source: https://github.com/BtbN/FFmpeg-Builds/releases/latest" \
  "FFmpeg source: https://github.com/FFmpeg/FFmpeg" \
  "libmpv source: https://github.com/mpv-player/mpv" \
  > "$LICENSE_DIR/SOURCE_URLS.txt"

(
  cd "$THIRDPART_DIR"
  find . -type f ! -name 'third-party-manifest.sha256' -print0 \
    | sort -z \
    | xargs -0 sha256sum
) > "$THIRDPART_DIR/third-party-manifest.sha256"

echo "Prepared Linux runtime payload:"
cat "$THIRDPART_DIR/third-party-manifest.sha256"

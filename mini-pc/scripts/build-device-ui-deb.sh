#!/usr/bin/env bash
# Build a compiled MeetingBox Device UI .deb for the current CPU architecture.
# Run this on the same architecture you want to ship:
#   Raspberry Pi / ARM64 -> arm64 .deb
#   Intel/AMD mini PC    -> amd64 .deb

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
MINI_PC_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
DEVICE_UI_DIR="$MINI_PC_ROOT/device-ui"

PACKAGE_NAME="${PACKAGE_NAME:-meetingbox-ui}"
VERSION="${VERSION:-0.1.0}"
ARCH="${ARCH:-$(dpkg --print-architecture)}"
BUILD_ROOT="${BUILD_ROOT:-$DEVICE_UI_DIR/build/native-deb}"
BUILD_VENV="${BUILD_VENV:-$DEVICE_UI_DIR/.build-native-venv}"
NATIVE_BUILD_DIR="$BUILD_ROOT/nuitka"
PKG_ROOT="$BUILD_ROOT/pkg-root"
OUT_DIR="${OUT_DIR:-$MINI_PC_ROOT/dist}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This build script must run on Linux, preferably on the target architecture." >&2
  exit 1
fi

for cmd in dpkg-deb python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
done

if [[ ! -f "$DEVICE_UI_DIR/src/main.py" ]]; then
  echo "Cannot find device-ui source at $DEVICE_UI_DIR" >&2
  exit 1
fi

mkdir -p "$BUILD_ROOT" "$OUT_DIR"
python3 -m venv "$BUILD_VENV"
# shellcheck source=/dev/null
source "$BUILD_VENV/bin/activate"
python -m pip install --upgrade pip wheel setuptools
python -m pip install -r "$DEVICE_UI_DIR/requirements.txt"
python -m pip install nuitka ordered-set zstandard

rm -rf "$NATIVE_BUILD_DIR" "$PKG_ROOT"
mkdir -p "$NATIVE_BUILD_DIR" "$PKG_ROOT"

echo "Building native binary with Nuitka..."
(
  cd "$DEVICE_UI_DIR"
  PYTHONPATH="$DEVICE_UI_DIR/src" python -m nuitka \
    --standalone \
    --onefile \
    --follow-imports \
    --assume-yes-for-downloads \
    --output-dir="$NATIVE_BUILD_DIR" \
    --output-filename=meetingbox-ui.bin \
    --include-data-dir="$DEVICE_UI_DIR/assets=assets" \
    ${NUITKA_EXTRA_ARGS:-} \
    "$DEVICE_UI_DIR/src/main.py"
)

BIN_PATH="$NATIVE_BUILD_DIR/meetingbox-ui.bin"
if [[ ! -x "$BIN_PATH" ]]; then
  echo "Nuitka did not produce $BIN_PATH" >&2
  exit 1
fi

install -d \
  "$PKG_ROOT/DEBIAN" \
  "$PKG_ROOT/usr/bin" \
  "$PKG_ROOT/usr/sbin" \
  "$PKG_ROOT/usr/lib/meetingbox/device-ui" \
  "$PKG_ROOT/usr/share/meetingbox/device-ui" \
  "$PKG_ROOT/etc/meetingbox" \
  "$PKG_ROOT/usr/local/bin" \
  "$PKG_ROOT/lib/systemd/system"

install -m 0755 "$BIN_PATH" "$PKG_ROOT/usr/lib/meetingbox/device-ui/meetingbox-ui.bin"
cp -a "$DEVICE_UI_DIR/assets" "$PKG_ROOT/usr/share/meetingbox/device-ui/assets"
install -m 0755 "$DEVICE_UI_DIR/packaging/native/meetingbox-ui" "$PKG_ROOT/usr/bin/meetingbox-ui"
install -m 0755 "$DEVICE_UI_DIR/packaging/native/meetingbox-install-native-kiosk" "$PKG_ROOT/usr/sbin/meetingbox-install-native-kiosk"
install -m 0755 "$DEVICE_UI_DIR/packaging/native/xinitrc-meetingbox-ui" "$PKG_ROOT/usr/share/meetingbox/device-ui/xinitrc-meetingbox-ui"
install -m 0644 "$DEVICE_UI_DIR/packaging/native/device-ui.env" "$PKG_ROOT/etc/meetingbox/device-ui.env"
install -m 0644 "$MINI_PC_ROOT/kiosk-desktop/panel-xrandr.env.example" "$PKG_ROOT/etc/meetingbox/panel-xrandr.env"
install -m 0755 "$MINI_PC_ROOT/scripts/apply-kiosk-display-orientation.sh" "$PKG_ROOT/usr/local/bin/meetingbox-apply-kiosk-display-orientation"
install -m 0644 "$DEVICE_UI_DIR/packaging/native/meetingbox-ui.service" "$PKG_ROOT/lib/systemd/system/meetingbox-ui.service"

cat >"$PKG_ROOT/DEBIAN/control" <<EOF
Package: $PACKAGE_NAME
Version: $VERSION
Section: misc
Priority: optional
Architecture: $ARCH
Maintainer: MeetingBox <support@example.invalid>
Depends: libc6, libstdc++6, libgl1, libgles2, libsdl2-2.0-0, libsdl2-image-2.0-0, libsdl2-mixer-2.0-0, libsdl2-ttf-2.0-0, libmtdev1t64 | libmtdev1, libportaudio2, xclip, x11-xserver-utils, xinit, xorg, openbox, iproute2, network-manager, espeak-ng
Description: MeetingBox native device UI
 Fullscreen Kivy touch interface for MeetingBox appliance devices.
EOF

cat >"$PKG_ROOT/DEBIAN/conffiles" <<'EOF'
/etc/meetingbox/device-ui.env
/etc/meetingbox/panel-xrandr.env
EOF

cat >"$PKG_ROOT/DEBIAN/postinst" <<'EOF'
#!/usr/bin/env bash
set -e
systemctl daemon-reload >/dev/null 2>&1 || true
mkdir -p /opt/meetingbox/data/config
chmod 0755 /opt/meetingbox /opt/meetingbox/data /opt/meetingbox/data/config 2>/dev/null || true
exit 0
EOF
chmod 0755 "$PKG_ROOT/DEBIAN/postinst"

cat >"$PKG_ROOT/DEBIAN/prerm" <<'EOF'
#!/usr/bin/env bash
set -e
if [[ "${1:-}" = "remove" ]]; then
  systemctl stop meetingbox-ui.service >/dev/null 2>&1 || true
fi
exit 0
EOF
chmod 0755 "$PKG_ROOT/DEBIAN/prerm"

cat >"$PKG_ROOT/DEBIAN/postrm" <<'EOF'
#!/usr/bin/env bash
set -e
systemctl daemon-reload >/dev/null 2>&1 || true
exit 0
EOF
chmod 0755 "$PKG_ROOT/DEBIAN/postrm"

DEB="$OUT_DIR/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"
dpkg-deb --build "$PKG_ROOT" "$DEB"

echo "Built: $DEB"
echo "Install on device: sudo apt install ./${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"

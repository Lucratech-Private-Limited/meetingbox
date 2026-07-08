#!/usr/bin/env bash
# Build a compiled MeetingBox *Flutter* Device UI .deb for the current CPU arch.
# Ships the native Flutter Linux bundle + the Python device-services bridge,
# with systemd units for both. Run on the same architecture you want to ship
# (Intel/AMD mini PC -> amd64, ARM64 board -> arm64).
#
# Prereqs on the build host (see Phase 8 of the parity plan):
#   sudo apt install -y clang cmake ninja-build pkg-config libgtk-3-dev \
#                       python3-venv dpkg-dev
#   Flutter SDK on PATH (flutter --version)

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
MINI_PC_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
FLUTTER_DIR="$MINI_PC_ROOT/device-ui-flutter"
SERVICES_DIR="$MINI_PC_ROOT/device-services"
PKG_SRC="$FLUTTER_DIR/packaging"

PACKAGE_NAME="${PACKAGE_NAME:-meetingbox-ui-flutter}"
VERSION="${VERSION:-0.1.0}"
ARCH="${ARCH:-$(dpkg --print-architecture)}"
BUILD_ROOT="${BUILD_ROOT:-$FLUTTER_DIR/build/native-deb}"
PKG_ROOT="$BUILD_ROOT/pkg-root"
OUT_DIR="${OUT_DIR:-$MINI_PC_ROOT/dist}"

# Build-time backend config baked into the binary via --dart-define.
ENV_FILE="${ENV_FILE:-$PKG_SRC/device-ui-flutter.env}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This build script must run on Linux, preferably on the target architecture." >&2
  exit 1
fi

for cmd in dpkg-deb flutter python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
done

if [[ ! -f "$FLUTTER_DIR/pubspec.yaml" ]]; then
  echo "Cannot find Flutter app at $FLUTTER_DIR" >&2
  exit 1
fi

# ---- 1. Read dart-define values from the env file --------------------------
declare -a DART_DEFINES=()
add_define() {
  local key="$1"
  local val
  val="$(sed -n "s/^${key}=//p" "$ENV_FILE" 2>/dev/null | tail -n 1 || true)"
  if [[ -n "$val" ]]; then
    DART_DEFINES+=("--dart-define=${key}=${val}")
  fi
}
if [[ -f "$ENV_FILE" ]]; then
  for k in BACKEND_URL BACKEND_WS_URL DASHBOARD_PUBLIC_URL MOCK_BACKEND \
           DEVICE_BRIDGE_URL DISPLAY_WIDTH DISPLAY_HEIGHT FULLSCREEN; do
    add_define "$k"
  done
fi

# ---- 2. Scaffold Linux runner (idempotent) + build release bundle ----------
(
  cd "$FLUTTER_DIR"
  if [[ ! -d linux ]]; then
    echo "Scaffolding Linux runner..."
    flutter create --platforms=linux .
  fi
  # Apply the kiosk runner (fullscreen + no title bar, honors FULLSCREEN env).
  # flutter create regenerates the default windowed runner, so overwrite it.
  if [[ -f "$PKG_SRC/my_application.cc" && -d linux/runner ]]; then
    echo "Applying kiosk fullscreen runner..."
    cp "$PKG_SRC/my_application.cc" linux/runner/my_application.cc
  fi
  flutter pub get
  echo "Building Flutter Linux release bundle..."
  flutter build linux --release "${DART_DEFINES[@]}"
)

BUNDLE_DIR="$FLUTTER_DIR/build/linux/$(uname -m | sed 's/x86_64/x64/;s/aarch64/arm64/')/release/bundle"
if [[ ! -d "$BUNDLE_DIR" ]]; then
  # Fall back to globbing in case the arch dir name differs.
  BUNDLE_DIR=$(find "$FLUTTER_DIR/build/linux" -type d -name bundle | head -n 1 || true)
fi
if [[ -z "${BUNDLE_DIR:-}" || ! -d "$BUNDLE_DIR" ]]; then
  echo "Flutter build did not produce a Linux bundle." >&2
  exit 1
fi
echo "Using bundle: $BUNDLE_DIR"

# ---- 3. Lay out the package tree -------------------------------------------
rm -rf "$PKG_ROOT"
install -d \
  "$PKG_ROOT/DEBIAN" \
  "$PKG_ROOT/usr/bin" \
  "$PKG_ROOT/usr/lib/meetingbox/device-ui-flutter" \
  "$PKG_ROOT/usr/lib/meetingbox/device-services/src" \
  "$PKG_ROOT/etc/meetingbox" \
  "$PKG_ROOT/lib/systemd/system"

cp -a "$BUNDLE_DIR/." "$PKG_ROOT/usr/lib/meetingbox/device-ui-flutter/"
cp -a "$SERVICES_DIR/src/." "$PKG_ROOT/usr/lib/meetingbox/device-services/src/"
install -m 0644 "$SERVICES_DIR/requirements.txt" "$PKG_ROOT/usr/lib/meetingbox/device-services/requirements.txt"

install -m 0755 "$PKG_SRC/meetingbox-flutter-ui" "$PKG_ROOT/usr/bin/meetingbox-flutter-ui"
install -m 0644 "$PKG_SRC/device-ui-flutter.env" "$PKG_ROOT/etc/meetingbox/device-ui-flutter.env"
install -m 0644 "$PKG_SRC/meetingbox-flutter-ui.service" "$PKG_ROOT/lib/systemd/system/meetingbox-flutter-ui.service"
install -m 0644 "$PKG_SRC/meetingbox-device-bridge.service" "$PKG_ROOT/lib/systemd/system/meetingbox-device-bridge.service"

# ---- 4. Debian control + maintainer scripts --------------------------------
cat >"$PKG_ROOT/DEBIAN/control" <<EOF
Package: $PACKAGE_NAME
Version: $VERSION
Section: misc
Priority: optional
Architecture: $ARCH
Maintainer: MeetingBox <support@example.invalid>
Depends: libc6, libstdc++6, libgtk-3-0, libglib2.0-0, libgl1, python3, python3-venv, iproute2, network-manager
Conflicts: meetingbox-ui
Description: MeetingBox Flutter device UI + local services bridge
 Native Flutter kiosk UI for MeetingBox appliances, plus the Python
 device-services bridge (FastAPI) for local hardware control. Replaces the
 legacy Kivy 'meetingbox-ui' package.
EOF

cat >"$PKG_ROOT/DEBIAN/conffiles" <<'EOF'
/etc/meetingbox/device-ui-flutter.env
EOF

cat >"$PKG_ROOT/DEBIAN/postinst" <<'EOF'
#!/usr/bin/env bash
set -e

SERVICES_DIR=/usr/lib/meetingbox/device-services

# Create an isolated venv for the bridge and install its deps.
if [[ ! -x "$SERVICES_DIR/venv/bin/python" ]]; then
  python3 -m venv "$SERVICES_DIR/venv"
fi
"$SERVICES_DIR/venv/bin/python" -m pip install --upgrade pip wheel >/dev/null 2>&1 || true
"$SERVICES_DIR/venv/bin/python" -m pip install -r "$SERVICES_DIR/requirements.txt" >/dev/null 2>&1 || true

mkdir -p /opt/meetingbox/data/config
chmod 0755 /opt/meetingbox /opt/meetingbox/data /opt/meetingbox/data/config 2>/dev/null || true

systemctl daemon-reload >/dev/null 2>&1 || true
systemctl enable meetingbox-device-bridge.service >/dev/null 2>&1 || true
systemctl enable meetingbox-flutter-ui.service >/dev/null 2>&1 || true
systemctl restart meetingbox-device-bridge.service >/dev/null 2>&1 || true
systemctl restart meetingbox-flutter-ui.service >/dev/null 2>&1 || true
exit 0
EOF
chmod 0755 "$PKG_ROOT/DEBIAN/postinst"

cat >"$PKG_ROOT/DEBIAN/prerm" <<'EOF'
#!/usr/bin/env bash
set -e
if [[ "${1:-}" = "remove" ]]; then
  systemctl stop meetingbox-flutter-ui.service >/dev/null 2>&1 || true
  systemctl stop meetingbox-device-bridge.service >/dev/null 2>&1 || true
fi
exit 0
EOF
chmod 0755 "$PKG_ROOT/DEBIAN/prerm"

cat >"$PKG_ROOT/DEBIAN/postrm" <<'EOF'
#!/usr/bin/env bash
set -e
if [[ "${1:-}" = "purge" ]]; then
  rm -rf /usr/lib/meetingbox/device-services/venv
fi
systemctl daemon-reload >/dev/null 2>&1 || true
exit 0
EOF
chmod 0755 "$PKG_ROOT/DEBIAN/postrm"

# ---- 5. Build the .deb -----------------------------------------------------
mkdir -p "$OUT_DIR"
DEB="$OUT_DIR/${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"
dpkg-deb --build "$PKG_ROOT" "$DEB"

echo "Built: $DEB"
echo
echo "Install on device:  sudo apt install ./${PACKAGE_NAME}_${VERSION}_${ARCH}.deb"
echo "Rollback to Kivy:   sudo apt install ./meetingbox-ui_<version>_${ARCH}.deb"

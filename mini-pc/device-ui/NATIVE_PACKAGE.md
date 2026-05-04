# Native Linux package

This is the production path for shipping the Device UI without cloning source code onto the appliance.

The build machine turns `device-ui` into a compiled Linux binary with Nuitka, wraps it in a Debian package, and the device installs only the `.deb`.

## What gets installed

The `.deb` installs:

```text
/usr/bin/meetingbox-ui
/usr/lib/meetingbox/device-ui/meetingbox-ui.bin
/usr/share/meetingbox/device-ui/assets/
/etc/meetingbox/device-ui.env
/etc/meetingbox/panel-xrandr.env
/lib/systemd/system/meetingbox-ui.service
/usr/sbin/meetingbox-install-native-kiosk
```

It does not install the git repo or Python source tree.

## Build

Build on the same CPU architecture you want to ship.

Raspberry Pi 64-bit:

```bash
cd ~/meetingbox-mini-pc-release
VERSION=1.0.0 bash scripts/build-device-ui-deb.sh
```

Intel/AMD mini PC:

```bash
cd ~/meetingbox-mini-pc-release
VERSION=1.0.0 bash scripts/build-device-ui-deb.sh
```

The output is written to:

```text
mini-pc/dist/meetingbox-ui_1.0.0_arm64.deb
mini-pc/dist/meetingbox-ui_1.0.0_amd64.deb
```

depending on the build machine architecture.

## Install on a device

Copy the correct `.deb` to the device, then install it:

```bash
sudo apt install ./meetingbox-ui_1.0.0_arm64.deb
```

Edit runtime config:

```bash
sudo nano /etc/meetingbox/device-ui.env
```

Set at least:

```env
BACKEND_URL=https://your-server.example.com
BACKEND_WS_URL=wss://your-server.example.com/ws
DASHBOARD_URL=https://your-server.example.com/
FULLSCREEN=1
DISPLAY_WIDTH=1024
DISPLAY_HEIGHT=600
```

Run manually from an existing X11 session:

```bash
meetingbox-ui
```

Or run with systemd when a graphical session already exists:

```bash
sudo systemctl enable meetingbox-ui
sudo systemctl start meetingbox-ui
```

## Appliance boot on Ubuntu Server

For a no-desktop appliance boot, keep SSH working first, then run:

```bash
MEETINGBOX_I_KNOW=1 sudo meetingbox-install-native-kiosk
sudo reboot
```

This configures tty1 auto-login, starts a minimal X11 session, launches Openbox, and runs `meetingbox-ui` fullscreen. It disables the normal GDM desktop/login path if it exists.

## Notes

- This protects the source code much better than shipping `.py` files, but it is not impossible to reverse engineer.
- Build separate artifacts for `arm64` and `amd64`.
- Do not put master secrets in `/etc/meetingbox/device-ui.env`; device tokens should be revocable server-side.
- If Nuitka misses a dynamic import on a specific build, pass extra flags with `NUITKA_EXTRA_ARGS="..."`.

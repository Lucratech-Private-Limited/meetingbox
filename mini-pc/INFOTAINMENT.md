# Infotainment-style boot (one app, no Ubuntu desktop)

Goal: power on → **MeetingBox fullscreen** in Docker, **without** GNOME (no dock, Activities, or normal Ubuntu desktop). Under the hood it is still Linux + Docker (like many car head units).

### Panel rotation / DSI mode (permanent)

At kiosk login, **`meetingbox-gdm-kiosk-session`** runs **`/usr/local/bin/meetingbox-apply-kiosk-display-orientation`**, which reads **`/etc/meetingbox/panel-xrandr.env`** (installed once by **`install-gdm-kiosk-session.sh`** from **`kiosk-desktop/panel-xrandr.env.example`**). Defaults: **`DSI-1`**, **`800x1280`**, **`rotate right`**. Edit that file to match your **`xrandr`** output, then reboot. Set **`MEETINGBOX_SKIP_PANEL_XRANDR=1`** there to disable. For Docker UI scaling, set **`MEETINGBOX_SYNC_DISPLAY_FROM_XRANDR=1`** or **`DISPLAY_WIDTH` / `DISPLAY_HEIGHT`** in **`.env`** to match **`xrandr`** after rotation.

**Touch misaligned or not working after rotation:** the same script runs **`xinput map-to-output`** so the touchscreen is tied to the same output as the panel. **`xinput`** is installed with **`install-gdm-kiosk-session.sh`**. Set **`MEETINGBOX_TOUCH_XINPUT_DEVICE`** to the exact line from **`xinput list --name-only`** if needed. If the **UI looks right but taps hit the wrong spot**, add **`MEETINGBOX_TOUCH_MATRIX_PRESET=right`** (for **`--rotate right`**) or **`MEETINGBOX_TOUCH_COORD_MATRIX`** with nine numbers — see **`kiosk-desktop/panel-xrandr.env.example`**. Also set **`MEETINGBOX_SYNC_DISPLAY_FROM_XRANDR=1`** or **`DISPLAY_WIDTH` / `DISPLAY_HEIGHT`** in **mini-pc `.env`** to match **`xrandr`** after rotation (wrong Kivy size skews hit targets). Add the GUI user to **`input`**: **`sudo usermod -aG input meetingbox`**. **`sudo evtest`** checks the kernel.

## Why the device showed “Ubuntu” for minutes

Two separate issues are easy to confuse:

1. **Full Ubuntu / GNOME session** — If you only ran `install-boot-service.sh` and auto-login still uses the **Ubuntu** session, the machine spends a long time loading the full desktop before anything looks appliance-like. Fix: use the **MeetingBox kiosk** session (this doc) so boot goes **GDM flash → black screen → MeetingBox**, not the purple wallpaper and dock.

2. **`network-online.target` stalls** — Older appliance units waited on systemd’s “network is fully online” step, which can sit for **minutes** on slow DNS or misconfigured networks **before** `kiosk-compose-up.sh` even ran. Current `scripts/install-boot-service.sh` uses **`network.target` only** for ordering. **Re-run** that installer once after upgrading this repo so `/etc/systemd/system/meetingbox-*.service` picks up the change:  
   `sudo bash scripts/install-boot-service.sh`

To see what blocked boot: `systemd-analyze critical-chain meetingbox-appliance.service` (after a reboot).

## One command (after `.env` exists)

```bash
cd ~/meetingbox-mini-pc-release

# Once: create config (BACKEND_URL, COMPOSE_PROFILES=mini-pc,docker-audio, XAUTHORITY_HOST, …)
cp -n .env.example .env && nano .env

# Once: Docker permission for the GUI user
sudo usermod -aG docker meetingbox
# log out/in or reboot so group applies, then:

sudo bash scripts/setup-infotainment-kiosk.sh
sudo reboot
```

What this installs:

| Piece | Role |
|--------|------|
| **MeetingBox Kiosk** (GDM X session) | Black screen + Openbox; no full Ubuntu session |
| **`/etc/gdm3/custom.conf`** | Auto-login straight into `meetingbox-kiosk` |
| **`meetingbox-docker-audio.service`** | Redis + audio containers at **multi-user** (no display needed) |
| **`meetingbox-appliance.service`** | After graphical boot: cookie + `docker compose up -d` (UI + stack) |

## What you will still see

- Motherboard / UEFI logo  
- Short **kernel / Plymouth** (optional: `quiet splash` in GRUB)  
- A **brief** GDM/video-mode moment before the black screen  

Removing **all** branding needs a custom OEM image, not this repo alone.

## Kiosk “not happening” (still Ubuntu desktop, or black screen forever)

On the device (SSH is fine), collect a quick report:

```bash
cd ~/meetingbox-mini-pc-release
bash scripts/diagnose-kiosk-boot.sh
```

Typical causes:

- **Still on the Ubuntu session** — `XSession` must be `meetingbox-kiosk` and GDM must auto-login into that session (see script output). Re-run `sudo bash scripts/setup-infotainment-kiosk.sh` then reboot.
- **`meetingbox` not in group `docker`** — kiosk session cannot run `docker compose`. `sudo usermod -aG docker meetingbox`, then log out completely or reboot.
- **No containers** — ensure `.env` exists; if it has no `COMPOSE_PROFILES=`, current `kiosk-compose-up.sh` defaults to `mini-pc,docker-audio`. Check `journalctl -t meetingbox-kiosk-compose -b` and `docker ps -a`.
- **`meetingbox-appliance.service` failed with `status=203/EXEC`** — the systemd unit could not run `kiosk-compose-up.sh` (bad shebang/CRLF or invalid `Documentation=` in an older unit). Re-run `sudo bash scripts/install-boot-service.sh` and `sudo systemctl daemon-reload` after pulling the repo (current units use `ExecStart=/usr/bin/bash …/kiosk-compose-up.sh` and a valid `Documentation=` URL).

## SSH recovery (panel blank / no Docker)

```bash
cd ~/meetingbox-mini-pc-release
bash scripts/recovery-appliance-ssh.sh
sudo systemctl start meetingbox-docker-audio meetingbox-appliance
docker ps
```

## Back to normal Ubuntu desktop

1. Remove the block between `# --- MeetingBox kiosk autologin ---` and `# --- end MeetingBox ---` in `/etc/gdm3/custom.conf` (backups: `*.bak-meetingbox-*` nearby).  
2. Edit `/var/lib/AccountsService/users/meetingbox` → `XSession=ubuntu` (or delete the `XSession=` line).  
3. `sudo reboot`

## Even less GDM (advanced)

To avoid the GDM greeter entirely, see **Level B** in `README.md` (`install-xinit-no-gdm.sh`). Keep SSH working before trying.

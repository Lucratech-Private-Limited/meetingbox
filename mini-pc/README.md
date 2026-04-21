# MeetingBox mini PC (appliance)

This folder contains everything that normally runs on the **meeting room device**: the **Kivy device UI** and the **audio capture** stack. The FastAPI dashboard lives in **`server/`** in the main repo (or your VPS uses a server-only clone).

## Contents

| Path | Purpose |
|------|---------|
| `device-ui/` | Touch/kiosk UI (Python/Kivy) |
| `audio/` | Mic capture, VAD, WAV upload (`run_audio_capture.sh` + Docker image) |
| `docker-compose.yml` | Optional: run UI and/or Docker audio on the device |
| `.env.example` | All appliance env vars — copy to `.env` |
| `scripts/install-boot-service.sh` | **systemd**: redis+audio @ multi-user + full stack @ graphical |
| `scripts/recovery-appliance-ssh.sh` | **SSH recovery** when Docker / UI stopped |
| `scripts/setup-infotainment-kiosk.sh` | **One-shot** GDM kiosk + systemd boot stack |
| `INFOTAINMENT.md` | Checklist: infotainment-style boot (no Ubuntu desktop) |
| `SETUP_PANEL_TOUCH.md` | **Step-by-step:** DSI rotation + touch alignment (`panel-xrandr.env`) |
| `scripts/diagnose-touch-panel.sh` | **Pasteable report** when touch/rotation still misbehaves |
| `scripts/diagnose-kiosk-boot.sh` | On-device checks when kiosk / Docker “is not happening” |
| `scripts/install-gdm-kiosk-session.sh` | GDM **direct** into kiosk session + `custom.conf` autologin |
| `scripts/install-xinit-no-gdm.sh` | **Disable GDM** — tty1 `startx` → app only (advanced) |
| `scripts/revert-xinit-no-gdm.sh` | Restore GDM / graphical target |
| `kiosk-desktop/meetingbox-kiosk.desktop` | GDM “MeetingBox Kiosk” session |
| `kiosk-desktop/xinitrc-meetingbox` | `startx` script when GDM is off |

## Quick start (mini PC only)

**Appliance on real hardware (firmware-like, no Ubuntu desktop):** after `.env` exists and the GUI user is in the `docker` group, run **`sudo bash scripts/setup-infotainment-kiosk.sh`** then reboot — see **`INFOTAINMENT.md`**. Skipping this and staying on the default Ubuntu session is why many installs show the normal desktop for a long time before MeetingBox appears.

```bash
cd mini-pc
cp .env.example .env
nano .env   # BACKEND_URL, BACKEND_WS_URL, UPLOAD_AUDIO_API_URL, DASHBOARD_URL
mkdir -p data/audio/recordings data/audio/temp data/config
```

**UI (recommended native):**

```bash
cd device-ui
cp .env.example .env   # optional per-app overrides
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
./run_device_ui.sh
```

**Mic (recommended host script):**

```bash
cd audio
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
# Ensure .env or exports: REDIS_HOST, UPLOAD_AUDIO_API_URL, DEVICE_AUTH_TOKEN after pairing
./run_audio_capture.sh
```

**UI + Docker mic** — `redis` and `audio` are gated by the **`docker-audio`** profile. Either set in `.env`:

```env
COMPOSE_PROFILES=mini-pc,docker-audio
```

or pass profiles on the CLI:

```bash
docker compose --profile mini-pc --profile docker-audio up -d --build
```

If you only set `COMPOSE_PROFILES=mini-pc`, the mic and Redis containers **will not start** (by design).

## Kiosk: fullscreen UI + start on boot

**Infotainment setup (recommended):** see **`INFOTAINMENT.md`** or run:

```bash
cd ~/meetingbox-mini-pc-release
sudo bash scripts/setup-infotainment-kiosk.sh && sudo reboot
```

(after `.env` exists and the GUI user is in the `docker` group).

The device-ui image defaults to **borderless fullscreen** (`FULLSCREEN` defaults to `1` in `docker-compose.yml`). Override with `FULLSCREEN=0` in `.env` when developing on a desktop.

**Permanent X11 settings** belong in `.env` on the device (not only in your SSH shell): `DEVICE_UI_DISPLAY`, `XAUTHORITY_HOST`, and `FULLSCREEN=1`. Copy from `.env.example` and adjust the username in `XAUTHORITY_HOST`.

**systemd (after graphical login / auto-login):**

```bash
cd /path/to/meetingbox-mini-pc-release   # your install dir
cp .env.example .env   # if needed; edit BACKEND_URL, XAUTHORITY_HOST, etc.
sudo usermod -aG docker meetingbox        # GUI user; then re-login
sudo bash scripts/install-boot-service.sh  # optional: pass install dir as first arg
sudo systemctl start meetingbox-appliance
```

Install **`install-boot-service.sh`** to register two units: **`meetingbox-docker-audio.service`** (**`multi-user.target`**) starts **Redis + audio** without any display; **`meetingbox-appliance.service`** (**`graphical.target`**) runs **`scripts/kiosk-compose-up.sh`** (cookie + full **`docker compose up -d`** including the UI).

Configure **automatic login** so GDM creates that session at boot; otherwise log in once on the panel after each reboot before the wait window (about two minutes) expires.

For a “single app” feel, **do not rely on hiding GNOME panels** — use **`setup-infotainment-kiosk.sh`** so the device never loads the full Ubuntu session. You will still see a **brief** vendor/GDM/kernel moment; hiding **all** of that needs OEM boot splash / custom image (see **`INFOTAINMENT.md`**).

The boot script runs **`docker compose up -d` once** (no immediate `--force-recreate` of the UI) so the fullscreen app is not stopped and restarted a second time on every boot.

### Boot straight into the app — no Ubuntu desktop (two levels)

**Goal:** never show the normal Ubuntu/GNOME session (dock, Activities, purple wallpaper). The MeetingBox UI should appear at **login level**, not “on top of” a full desktop.

#### Level A — GDM auto-login → MeetingBox session only (recommended first)

Prefer **`sudo bash scripts/setup-infotainment-kiosk.sh`** (runs both installers below). Manual equivalent:

```bash
cd /path/to/meetingbox-mini-pc-release
sudo bash scripts/install-gdm-kiosk-session.sh
sudo bash scripts/install-boot-service.sh
sudo reboot
```

This installs a minimal **X session** (`meetingbox-kiosk`: black screen + Openbox + Docker UI) and patches **`/etc/gdm3/custom.conf`** so GDM **auto-logs in** with **`AutomaticLoginSession=meetingbox-kiosk`**. You should **not** get the Ubuntu desktop or session chooser — only a possible **brief** GDM/video mode flash before the black screen and app.

**Do not** `systemctl disable meetingbox-appliance` here — if the kiosk X session fails to run `docker compose`, nothing would start the UI. The boot installer also enables **`meetingbox-docker-audio.service`** so **Redis + mic** start even when the display stack is broken (recover over SSH).

The installer also sets **`WaylandEnable=false`** (stable X11 path) and **`XSession=meetingbox-kiosk`** in **AccountsService** as a fallback.

**Recovery (SSH):** `bash scripts/recovery-appliance-ssh.sh` then check `docker ps` and `journalctl -u meetingbox-docker-audio -u meetingbox-appliance -b`.

**Revert:** remove the `MeetingBox kiosk autologin` block from **`/etc/gdm3/custom.conf`** (backups are created beside it); set **`XSession=ubuntu`** in **`/var/lib/AccountsService/users/<you>`**; **`sudo systemctl enable gdm3`** if needed; reboot.

#### Level B — no GDM at all (no Ubuntu login UI)

Disables **GDM**, sets **`multi-user.target`**, **auto-login on tty1**, and runs **`startx`** with **`~/.xinitrc-meetingbox`**. You will **not** see the GDM greeter. You will still see **BIOS/kernel** text unless you tune **`quiet splash`** / Plymouth separately.

```bash
cd /path/to/meetingbox-mini-pc-release
MEETINGBOX_I_KNOW=1 sudo bash scripts/install-xinit-no-gdm.sh
sudo bash scripts/install-boot-service.sh   # early redis+audio on multi-user; optional graphical unit
sudo systemctl disable meetingbox-appliance.service   # OK here: default target is multi-user; ~/.xinitrc starts Compose
sudo reboot
```

Keep **SSH** available from another machine the first time. **Revert:** `sudo bash scripts/revert-xinit-no-gdm.sh`, then remove the **`# --- MEETINGBOX_XINIT_...`** block from **`~/.profile`** if you want it gone, reboot.

**Honest limit:** hiding **all** vendor branding (BIOS logo, Ubuntu plymouth) needs an **OEM/custom image**, not application code alone.

## Splitting into its own git repository

From the monorepo root (preserve history for this subtree):

```bash
git subtree split --prefix=mini-pc -b mini-pc-release
git push <your-appliance-remote> mini-pc-release:main
```

On the device, clone that repo and use only this directory — no `server/` or `frontend/` checkout required. (`run_device_ui.sh` / `run_audio_capture.sh` look for a sibling `server/docker-compose.yml` only to detect the full monorepo and load a parent `.env`; that path is absent in an appliance-only clone and scripts still work.)

## Monorepo usage

The parent **`docker-compose.yml`** still builds `mini-pc/device-ui` and `mini-pc/audio` when you use profiles `mini-pc` / `docker-audio` there. This folder’s `docker-compose.yml` is for **appliance-only** checkouts.
